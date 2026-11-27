#!/bin/sh
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0
#
# Laptop-scale drive of the address `hs relay` tells its site clients. An upstream `hs server
# --forever` on 127.0.0.1 (cleartext), and a relay `--bind 127.0.0.1` whose launcher records each
# site client's command line. No scheduler, no remote host. Keys are given only by
# HYPERSHELL_SERVER_AUTH (upstream server) and HYPERSHELL_RELAY_UPSTREAM_AUTH (relay), never on a command line.
# Run it in a throwaway site, from the repository root:
#
#   .agents/factory/bin/temp_site.sh sh "$PWD/spec/relay-advertise/drive/advertise_drive.sh" MODE
#
# Modes and checks:
#   told     every site client is told: 127.0.0.1 with no override; localhost with --advertise
#            localhost, for both zones with --zone a --zone b; localhost with
#            HYPERSHELL_RELAY_ADVERTISE=localhost; 127.0.0.1 with --advertise 127.0.0.1 over that key
#            (no-such-name.invalid); 127.0.0.1 with HYPERSHELL_SERVER_ADVERTISE=localhost alone.
#   empty    --advertise '' and --advertise ' ' each exit 2 naming --advertise, with no relay
#            database created.
#   healthy  --advertise localhost and a launcher that runs the client: every upstream task completes.
#
# Settings: AD_WAIT (default 60), seconds any one relay or server may take.
# Every process the drive starts is stopped by its recorded PID before it exits.
set -eu

mode=${1:?usage: advertise_drive.sh told|empty|healthy}
wait_secs=${AD_WAIT:-60}
site=$PWD
bin=$site/bin
mkdir -p "$bin"
HS=$(cd "$UV_PROJECT" && uv run --quiet python -c 'import os, sys; print(os.path.join(os.path.dirname(sys.executable), "hs"))')
PATH=$(dirname "$HS"):$PATH; export PATH  # Site clients are launched as `hyper-shell client`.
key=advertise-drive-upstream-key-0123456789
pids=''

# rec.sh records its argv to $REC_LOG and exits (the relay launches again); run.sh records, then runs it.
cat > "$bin/rec.sh" <<'EOF'
#!/bin/sh
{ printf 'launch:'; for w in "$@"; do printf " '%s'" "$w"; done; printf '\n'; } >> "$REC_LOG"
EOF
cat > "$bin/run.sh" <<'EOF'
#!/bin/sh
{ printf 'launch:'; for w in "$@"; do printf " '%s'" "$w"; done; printf '\n'; } >> "$REC_LOG"
exec "$@"
EOF
chmod +x "$bin/rec.sh" "$bin/run.sh"

fail() { echo "advertise_drive[$mode]: $*" >&2; exit 1; }

# stop_tree PID [SIGNAL]: signal PID and every process under it (TERM by default).
stop_tree() {
    for child in $(ps -axo pid=,ppid= 2>/dev/null | awk -v p="$1" '$2 == p { print $1 }'); do stop_tree "$child" "${2:-TERM}"; done
    kill "-${2:-TERM}" "$1" 2>/dev/null || true
}
cleanup() {
    for pid in $pids; do stop_tree "$pid"; done
    sleep 1
    for pid in $pids; do stop_tree "$pid" KILL; done
}
trap cleanup EXIT INT TERM

free_port() { "$(dirname "$HS")/python" -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])'; }

# upstream [ARGS...]: start the upstream server on 127.0.0.1:$up (sets up, server_pid).
# Each check below also expects it still running after its relay drained.
upstream() {
    up=$(free_port)
    HYPERSHELL_SERVER_AUTH=$key "$HS" server --forever --no-tls -H 127.0.0.1 -p "$up" "$@" \
        < /dev/null > server.out 2> server.err &
    server_pid=$!; pids="$pids $server_pid"
    sleep 2
    kill -0 "$server_pid" 2>/dev/null || { cat server.err >&2; fail "upstream server exited"; }
}

# relay LABEL LAUNCHER ENV... -- ARGS...: start a relay (sets relay_pid); launches go to LABEL.rec.
relay() {
    label=$1 launcher=$2; shift 2
    envs=''
    while [ $# -gt 0 ] && [ "$1" != -- ]; do envs="$envs $1"; shift; done
    [ $# -gt 0 ] && shift
    REC_LOG=$site/$label.rec; export REC_LOG; : > "$REC_LOG"
    # shellcheck disable=SC2086
    env HYPERSHELL_RELAY_UPSTREAM_AUTH=$key $envs "$HS" relay -H 127.0.0.1 -p "$up" --no-tls \
        --bind 127.0.0.1 --port "$(free_port)" --database "$site/$label.db" --launcher "$bin/$launcher" "$@" \
        > "$label.out" 2> "$label.err" &
    relay_pid=$!; pids="$pids $relay_pid"
}

# launched LABEL ZONE...: wait until LABEL.rec holds a launch for each ZONE ('-' is the default zone).
launched() {
    label=$1; shift; start=$(date +%s)
    while :; do
        all=yes
        for zone in "$@"; do
            if [ "$zone" = - ]; then grep -q "'client'" "$label.rec" || all=no
            else grep -q "'--zone' '$zone'" "$label.rec" || all=no; fi
        done
        [ "$all" = yes ] && return 0
        kill -0 "$relay_pid" 2>/dev/null || { tail -5 "$label.err" >&2; fail "$label: relay exited"; }
        [ $(( $(date +%s) - start )) -lt "$wait_secs" ] || { tail -5 "$label.err" >&2; fail "$label: no launch for $*"; }
        sleep 0.5
    done
}

# drained LABEL: drain the relay (SIGUSR1) and expect exit 0 within the bound.
drained() {
    kill -USR1 "$relay_pid"; start=$(date +%s)
    while kill -0 "$relay_pid" 2>/dev/null; do
        [ $(( $(date +%s) - start )) -lt "$wait_secs" ] || fail "$1: relay still running after drain"
        sleep 0.5
    done
    set +e; wait "$relay_pid"; status=$?; set -e
    [ "$status" = 0 ] || { tail -5 "$1.err" >&2; fail "$1: relay exited $status after drain"; }
}

told() {  # told LABEL ADDR: every launch in LABEL.rec told the client ADDR
    [ -s "$1.rec" ] || fail "$1: nothing launched"
    if grep -v -e "'-H' '$2'" "$1.rec" | grep -q .; then
        fail "$1: a launch was told another address: $(grep -Eo -e "'-H' '[^']*'" "$1.rec" | sort -u | tr '\n' ' ')"
    fi
    echo "advertise_drive[$1]: told $2"
}

# check LABEL ADDR ZONES ENV... -- ARGS...: a relay with the recording launcher tells ADDR to ZONES.
check() {
    label=$1 addr=$2 zones=$3; shift 3
    relay "$label" rec.sh "$@"
    # shellcheck disable=SC2086
    launched "$label" $zones
    drained "$label"
    told "$label" "$addr"
    kill -0 "$server_pid" 2>/dev/null || { tail -5 server.err >&2; fail "$label: upstream server exited"; }
    if grep -q 'HYPERSHELL_RELAY_' "$label.rec"; then fail "$label: a relay variable reached a site command line"; fi
}

case $mode in
    told)
        upstream  # On the site database: a --no-db server ends once its input ends and its last relay drains.
        check none 127.0.0.1 - --
        check option localhost - -- --advertise localhost
        check zones localhost 'a b' -- --advertise localhost --zone a --zone b
        check key localhost - HYPERSHELL_RELAY_ADVERTISE=localhost --
        check both 127.0.0.1 - HYPERSHELL_RELAY_ADVERTISE=no-such-name.invalid -- --advertise 127.0.0.1
        check server-key 127.0.0.1 - HYPERSHELL_SERVER_ADVERTISE=localhost -- ;;
    empty)
        up=1  # Never dialed: the relay refuses its arguments first.
        for value in '' ' '; do
            label=empty$(printf '%s' "$value" | wc -c | tr -d ' ')
            relay "$label" rec.sh -- --advertise "$value"
            start=$(date +%s)
            while kill -0 "$relay_pid" 2>/dev/null; do
                [ $(( $(date +%s) - start )) -lt "$wait_secs" ] || fail "$label: still running"
                sleep 0.2
            done
            set +e; wait "$relay_pid"; status=$?; set -e
            [ "$status" = 2 ] || { cat "$label.err" >&2; fail "$label: status $status, expected 2"; }
            grep -q 'Expected an address for --advertise' "$label.err" || { cat "$label.err" >&2; fail "$label: no refusal naming --advertise"; }
            [ ! -e "$label.db" ] || fail "$label: relay database created"
            echo "advertise_drive[$label]: refused"
        done ;;
    healthy)
        printf 'echo %s\n' 1 2 3 4 5 6 > t.in
        HYPERSHELL_SERVER_AUTH=$key "$HS" submit t.in > submit.out 2> submit.err
        upstream
        relay healthy run.sh -- --advertise localhost
        start=$(date +%s)
        until [ "$("$HS" list --remaining --count 2>/dev/null)" = 0 ]; do
            kill -0 "$relay_pid" 2>/dev/null || { tail -5 healthy.err >&2; fail "healthy: relay exited"; }
            [ $(( $(date +%s) - start )) -lt "$wait_secs" ] || { tail -5 healthy.err >&2; fail "healthy: tasks remain"; }
            sleep 1
        done
        drained healthy
        told healthy localhost
        [ "$("$HS" list --failed --count)" = 0 ] || fail "healthy: a task failed"
        echo "advertise_drive[healthy]: $("$HS" list --count) tasks complete" ;;
    *) echo "usage: advertise_drive.sh told|empty|healthy" >&2; exit 2 ;;
esac
echo "advertise_drive[$mode]: ok"
