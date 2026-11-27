#!/bin/sh
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0
#
# Live drive of R5 for queue-wire-codec: this tree against a real 2.9.0a9 install, both ways, TLS on
# and off, in a throwaway site. Run from the worktree root:
#
#   .agents/factory/bin/temp_site.sh sh "$PWD/spec/queue-wire-codec/drive/old_peer.sh"
#
# The 2.9.0a9 install is the virtual environment at OLD_VENV (default
# /tmp/claude-501/venv314-codec/a9); if it has no `hs`, it is made there with
# `uv pip install hypershell==2.9.0a9`, which needs pypi.org and files.pythonhosted.org. Every server
# binds 127.0.0.1; keys are in HYPERSHELL_SERVER_AUTH and HYPERSHELL_RELAY_UPSTREAM_AUTH, never on a
# command line. Each command is bounded, and every process this drive starts is stopped before it exits.
#
#   1. This tree's `hs server --no-db`: a 2.9.0a9 `hs client`, then a 2.9.0a9 `hs submit --queue`, each
#      exits 5 within 10 s naming a release before 2.9.0a10, and the server logs one WARNING per
#      connection naming the peer and keeps running.
#   2. A 2.9.0a9 `hs server --no-db`: this tree's `hs client`, `hs submit --queue` and `hs relay`
#      (upstream), each exits 5 within 10 s with one CRITICAL line naming the older framing, and the
#      relay never tries again.
#
# Exits 0 when every check holds, else 1 at the first that does not (its output says which).

set -u

root=${UV_PROJECT:?run under .agents/factory/bin/temp_site.sh from the worktree root}
bounded=$root/spec/submit-input-checks/drive/bounded.py
bin=$(cd "$root" && uv run --quiet python -c 'import os, sys; print(os.path.dirname(sys.executable))') || exit 2
HS=$bin/hs
old=${OLD_VENV:-/tmp/claude-501/venv314-codec/a9}
if [ ! -x "$old/bin/hs" ]; then
    uv venv --quiet --python 3.14 "$old" && uv pip install --quiet --python "$old/bin/python" 'hypershell==2.9.0a9' \
        || { echo "old_peer: cannot install hypershell==2.9.0a9 at $old"; exit 2; }
fi
OLD=$old/bin/hs
version=$("$old/bin/python" -c 'import hypershell; print(hypershell.__version__)')
[ "$version" = 2.9.0a9 ] || { echo "old_peer: $old holds hypershell $version, not 2.9.0a9"; exit 2; }

started=''
stop_all() {
    for pid in $started; do kill -TERM "$pid" 2>/dev/null; done
    sleep 1
    for pid in $started; do kill -KILL "$pid" 2>/dev/null; done
    started=''
}
trap stop_all EXIT

fail() { echo "FAIL $*"; exit 1; }
pass() { echo "ok   $*"; }
free_port() { "$bin/python" -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])'; }
now() { "$bin/python" -c 'import time; print(time.monotonic())'; }
seconds() { "$bin/python" -c "print(f'{$(now) - $1:.1f}')"; }

# serve EXE LABEL: start `hs server --no-db` from EXE (sets server); its log is server-LABEL.err.
serve() {
    # shellcheck disable=SC2086
    HYPERSHELL_SERVER_QUEUESIZE=100 HYPERSHELL_LOGGING_LEVEL=DEBUG \
        "$1" server --no-db -H 127.0.0.1 -p "$port" $flag < /dev/null 2> "server-$2.err" &
    server=$!
    started="$started $server"
    n=0
    until grep -q 'Server listening' "server-$2.err"; do
        n=$((n + 1))
        [ "$n" -lt 100 ] && kill -0 "$server" 2>/dev/null || fail "$2: the server did not listen: $(tail -5 "server-$2.err")"
        sleep 0.1
    done
}

# refused LABEL WHAT FILE STATUS START: WHAT exited 5 within 10 s naming a release before 2.9.0a10.
refused() {
    label=$1 what=$2 file=$3 status=$4 start=$5
    took=$(seconds "$start")
    [ "$status" -eq 5 ] || fail "$label: $what exited $status, not 5: $(tail -5 "$file")"
    grep -q 'before 2.9.0a10' "$file" || fail "$label: $what did not name the version mismatch: $(tail -5 "$file")"
    critical=$(grep -c '^CRITICAL' "$file")
    echo "     $what: exit 5 in $took s, $critical CRITICAL line(s): $(grep -m 1 'before 2.9.0a10' "$file" | cut -c 1-160)"
}

for tls in tls no-tls; do
    flag=''
    [ "$tls" = no-tls ] && flag=--no-tls
    HYPERSHELL_SERVER_AUTH=$("$bin/python" -c 'import secrets; print(secrets.token_hex(16))')
    export HYPERSHELL_SERVER_AUTH

    # 1. A 2.9.0a9 client and submit against this tree's server
    label="new server ($tls)"
    port=$(free_port)
    serve "$HS" "new-$tls"
    for role in client submit; do
        case $role in
            client) set -- client -H 127.0.0.1 -p "$port" ;;
            submit) set -- submit --queue -H 127.0.0.1 -p "$port" echo old ;;
        esac
        before=$(grep -c 'Refused a queue connection from 127.0.0.1:' "server-new-$tls.err")
        start=$(now)
        # shellcheck disable=SC2086
        python3 "$bounded" 10 "$OLD" "$@" $flag > "old-$role-$tls.out" 2> "old-$role-$tls.err"
        refused "$label" "2.9.0a9 hs $role" "old-$role-$tls.err" $? "$start"
        sleep 0.5
        after=$(grep -c 'Refused a queue connection from 127.0.0.1:' "server-new-$tls.err")
        [ "$((after - before))" -eq 1 ] || fail "$label: $((after - before)) refusals logged for hs $role, not 1"
        grep 'Refused a queue connection from 127.0.0.1:' "server-new-$tls.err" | tail -n 1 | grep -q 'before 2.9.0a10' \
            || fail "$label: the refusal does not name the older framing"
    done
    kill -0 "$server" 2>/dev/null || fail "$label: the server exited after refusing"
    echo "     server: $(grep -m 1 'Refused a queue connection' "server-new-$tls.err" | cut -c 1-200)"
    if grep -E 'Traceback|ERROR|CRITICAL' "server-new-$tls.err" >/dev/null; then
        fail "$label: the server logged $(grep -E 'Traceback|ERROR|CRITICAL' "server-new-$tls.err" | head -3)"
    fi
    stop_all
    pass "$label: a 2.9.0a9 client and submit each exit 5 at once; one WARNING per connection; server serving"

    # 2. This tree's client, submit and relay against a 2.9.0a9 server
    label="old server ($tls)"
    port=$(free_port)
    serve "$OLD" "old-$tls"
    for role in client submit relay; do
        case $role in
            client) set -- client -H 127.0.0.1 -p "$port" ;;
            submit) set -- submit --queue -H 127.0.0.1 -p "$port" echo new ;;
            relay) set -- relay -H 127.0.0.1 -p "$port" --bind 127.0.0.1 --port "$(free_port)" \
                       --database "$PWD/relay-$tls.db" ;;
        esac
        start=$(now)
        # shellcheck disable=SC2086
        HYPERSHELL_RELAY_UPSTREAM_AUTH=$HYPERSHELL_SERVER_AUTH \
            python3 "$bounded" 10 "$HS" "$@" $flag > "new-$role-$tls.out" 2> "new-$role-$tls.err"
        refused "$label" "hs $role" "new-$role-$tls.err" $? "$start"
        [ "$(grep -c '^CRITICAL' "new-$role-$tls.err")" -eq 1 ] || fail "$label: hs $role: not one CRITICAL line"
        grep '^CRITICAL' "new-$role-$tls.err" | grep -q 'QueueVersionError' \
            || fail "$label: hs $role: the CRITICAL line is not the version error"
        if grep -E 'trying again|Reconnect|Still cannot reach' "new-$role-$tls.err" >/dev/null; then
            fail "$label: hs $role tried again"
        fi
    done
    stop_all
    pass "$label: this tree's client, submit and relay each exit 5 at once with one CRITICAL line"
    unset HYPERSHELL_SERVER_AUTH
done
echo "old_peer: every check holds"
