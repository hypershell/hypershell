#!/bin/sh
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0
#
# Laptop-scale drive of `hs cluster` when its launched clients fail, and of the address each launch
# mode tells its clients. Fake launchers stand in for a scheduler: `false` (every client exits 1),
# `true` (a launcher that exits 0 and leaves the clients to someone else), `env` (runs the client on
# this host), a recording launcher (logs its argv, then runs it), and a stub `ssh` first on PATH
# (logs its argv; runs the remote command here, or exits 255 like an unreachable host). No
# scheduler and no remote host are used. Run it in a throwaway site, from the repository root:
#
#   .agents/factory/bin/temp_site.sh sh "$PWD/spec/cluster-launch-failures/drive/launch_drive.sh" MODE
#
# Modes and checks:
#   fail            --no-db, --launcher false: exits non-zero within LD_FAIL seconds, after one error
#                   saying every launched client has exited, naming the launcher, status 1 and
#                   the address the clients were told.
#   fail-db         the same with a database.
#   zero            --launcher true (exits 0, work left): still running after LD_WAIT seconds,
#                   with no such error.
#   healthy         --launcher env, with and without a database: exits 0, every task's output
#                   printed, none remaining in the database.
#   ssh-fail        --ssh hostA,hostB, both stubs exit 255: exits non-zero within LD_FAIL seconds,
#                   naming both hosts and status 255.
#   ssh-partial     hostA's stub runs the client here, hostB's exits 255: exits 0 with every task
#                   done, and one warning naming hostB and status 255.
#   autoscale-fail  --autoscaling fixed -P 2s --launcher false: exits non-zero within LD_SCALE
#                   seconds, after an error naming 3 failed launches.
#   autoscale-off   the same with HYPERSHELL_AUTOSCALE_FAILED_LAUNCHES=0: still relaunching after
#                   LD_SCALE seconds, with no such error.
#   advertise       -H 127.0.0.1 in launcher, --ssh and autoscaling modes tells the clients
#                   127.0.0.1 and the work completes; --advertise localhost and server.advertise
#                   (HYPERSHELL_SERVER_ADVERTISE) tell them localhost, the option over the key;
#                   --advertise with a local cluster is refused (exit 2) naming the option and
#                   the local cluster.
#
# Every run binds 127.0.0.1 (or advertises localhost): this host's own name need not resolve here.
# Settings: LD_FAIL (default 15), LD_WAIT (default 12), LD_SCALE (default 30), in seconds.
# Every process the drive starts is stopped by its recorded PID before it exits.
set -eu

mode=${1:?usage: launch_drive.sh MODE}
fail_secs=${LD_FAIL:-15}
wait_secs=${LD_WAIT:-12}
scale_secs=${LD_SCALE:-30}
site=$PWD
bin=$site/bin
mkdir -p "$bin"
printf 'echo %s\n' 1 2 3 4 > t.in
envs=''  # NAME=value words (no spaces) the next run's command gets, then cleared

# The recording launcher: append its argv to $REC_LOG, then run it.
cat > "$bin/rec.sh" <<'EOF'
#!/bin/sh
{ printf 'launch:'; for w in "$@"; do printf " '%s'" "$w"; done; printf '\n'; } >> "$REC_LOG"
exec "$@"
EOF
# The stub ssh: append its argv to $REC_LOG; exit 255 for a host listed in STUB_SSH_FAIL (or every
# host when it is `all`); otherwise run the remote command here, as a remote shell would, with
# standard input passed through (the key arrives there, as with ssh).
cat > "$bin/ssh" <<'EOF'
#!/bin/sh
{ printf 'ssh:'; for w in "$@"; do printf " '%s'" "$w"; done; printf '\n'; } >> "$REC_LOG"
while [ $# -gt 0 ] && [ "$1" != -T ]; do shift; done
host=$2; shift 2
case " ${STUB_SSH_FAIL:-} " in *" all "*|*" $host "*) exit 255 ;; esac
exec sh -c "$*"
EOF
chmod +x "$bin/rec.sh" "$bin/ssh"

fail() { echo "launch_drive[$mode]: $*" >&2; exit 1; }

# stop_tree PID: stop PID and every process under it.
stop_tree() {
    for child in $(ps -axo pid=,ppid= 2>/dev/null | awk -v p="$1" '$2 == p { print $1 }'); do stop_tree "$child"; done
    kill "$1" 2>/dev/null || true
}

# run LABEL SECONDS ARGS...: run `hs cluster ARGS` (with the words in $envs added to its environment)
# in the background for at most SECONDS; a `t.in` among ARGS is replaced by a copy, LABEL.in. Sets status (the exit status, or `running` when it was
# still running and has been stopped) and elapsed; output is in LABEL.out and LABEL.err, launches in
# LABEL.rec. Clears $envs.
run() {
    label=$1 secs=$2; shift 2
    # Each run submits its own copy of the task file, so a database run never re-submits one.
    cp t.in "$label.in"; n=$#
    for word in "$@"; do [ "$word" = t.in ] && word=$label.in; set -- "$@" "$word"; done
    shift "$n"
    REC_LOG=$site/$label.rec; export REC_LOG; : > "$REC_LOG"
    start=$(date +%s)
    # shellcheck disable=SC2086
    env $envs uv run --quiet hs cluster "$@" > "$label.out" 2> "$label.err" &
    pid=$!; envs=''
    while kill -0 "$pid" 2>/dev/null && [ $(( $(date +%s) - start )) -lt "$secs" ]; do sleep 0.5; done
    elapsed=$(( $(date +%s) - start ))
    if kill -0 "$pid" 2>/dev/null; then
        status=running
        stop_tree "$pid"; sleep 1
        kill -9 "$pid" 2>/dev/null || true
        wait "$pid" 2>/dev/null || true
    else
        set +e; wait "$pid"; status=$?; set -e
    fi
    echo "launch_drive[$label]: $status after ${elapsed}s"
}

expect_exit() {  # expect_exit LABEL: a non-zero exit, not a timeout
    [ "$status" != running ] || { tail -5 "$1.err" >&2; fail "$1: still running after ${elapsed}s"; }
    [ "$status" != 0 ] || fail "$1: exited 0"
}
expect_ok() {  # expect_ok LABEL: exit 0 and every task's output
    [ "$status" = 0 ] || { tail -5 "$1.err" >&2; fail "$1: status $status"; }
    [ "$(sort -n "$1.out" | tr '\n' ' ')" = '1 2 3 4 ' ] || fail "$1: output was $(tr '\n' ' ' < "$1.out")"
}
expect_running() {  # expect_running LABEL: still running at the bound, with no exit error
    [ "$status" = running ] || { tail -5 "$1.err" >&2; fail "$1: ended with $status after ${elapsed}s"; }
    if grep -Eqi -e 'every launched client has exited|launched clients in a row' "$1.err"; then fail "$1: error logged"; fi
}
has() {  # has LABEL PATTERN: LABEL.err matches PATTERN (extended, case-insensitive)
    grep -Eqi -e "$2" "$1.err" || { tail -5 "$1.err" >&2; fail "$1: no '$2' in its log"; }
}
told() {  # told LABEL ADDR: every launch in LABEL.rec told the client ADDR
    [ -s "$1.rec" ] || fail "$1: nothing launched"
    if grep -Ev -e "'-H' '$2'|client -H $2 " "$1.rec" | grep -q .; then
        fail "$1: a launch was told another address: $(grep -Eo -e "'-H' '[^']*'|client -H [^ ]*" "$1.rec" | sort -u | tr '\n' ' ')"
    fi
}
remaining() { uv run --quiet hs list --remaining --count; }

common='--no-tls -H 127.0.0.1'
case $mode in
    fail)
        run fail "$fail_secs" t.in --no-db $common --launcher false
        expect_exit fail; has fail 'every launched client has exited'; has fail 'false'
        has fail 'status 1'; has fail '127\.0\.0\.1' ;;
    fail-db)
        run fail-db "$fail_secs" t.in $common --launcher false
        expect_exit fail-db; has fail-db 'every launched client has exited'; has fail-db 'status 1'
        has fail-db '127\.0\.0\.1' ;;
    zero)
        run zero "$wait_secs" t.in --no-db $common --launcher true
        expect_running zero ;;
    healthy)
        run healthy-nodb 60 t.in --no-db $common --launcher env
        expect_ok healthy-nodb
        run healthy-db 60 t.in $common --launcher env
        expect_ok healthy-db
        [ "$(remaining)" = 0 ] || fail "healthy-db: tasks remain" ;;
    ssh-fail)
        envs="PATH=$bin:$PATH STUB_SSH_FAIL=all"; run ssh-fail "$fail_secs" t.in $common --ssh hostA,hostB
        expect_exit ssh-fail; has ssh-fail 'every launched client has exited'
        has ssh-fail 'hostA'; has ssh-fail 'hostB'; has ssh-fail 'status 255' ;;
    ssh-partial)
        envs="PATH=$bin:$PATH STUB_SSH_FAIL=hostB"; run ssh-partial 60 t.in $common --ssh hostA,hostB
        expect_ok ssh-partial
        [ "$(remaining)" = 0 ] || fail "ssh-partial: tasks remain"
        [ "$(grep -Eci -e 'hostB.*255|255.*hostB' ssh-partial.err)" = 1 ] || fail "ssh-partial: expected one warning for hostB"
        if grep -qi 'every launched client has exited' ssh-partial.err; then fail "ssh-partial: error logged"; fi ;;
    autoscale-fail)
        run scale-fail "$scale_secs" t.in $common --autoscaling fixed -P 2s --launcher false
        expect_exit scale-fail; has scale-fail '3 launched clients in a row'; has scale-fail '127\.0\.0\.1' ;;
    autoscale-off)
        envs=HYPERSHELL_AUTOSCALE_FAILED_LAUNCHES=0; run scale-off "$scale_secs" t.in $common --autoscaling fixed -P 2s --launcher false
        expect_running scale-off
        [ "$(grep -ci 'exited with status' scale-off.err)" -ge 4 ] || fail "scale-off: not relaunching" ;;
    advertise)
        run adv-launch 60 t.in $common --launcher "$bin/rec.sh"
        expect_ok adv-launch; told adv-launch 127.0.0.1
        envs="PATH=$bin:$PATH"; run adv-ssh 60 t.in $common --ssh hostA
        expect_ok adv-ssh; told adv-ssh 127.0.0.1
        run adv-scale "$scale_secs" t.in $common --autoscaling fixed -P 2s --launcher "$bin/rec.sh"
        told adv-scale 127.0.0.1
        [ "$(remaining)" = 0 ] || fail "adv-scale: tasks remain"
        run adv-option 60 t.in --no-tls --advertise localhost --launcher "$bin/rec.sh"
        expect_ok adv-option; told adv-option localhost
        envs=HYPERSHELL_SERVER_ADVERTISE=localhost; run adv-key 60 t.in --no-tls --launcher "$bin/rec.sh"
        expect_ok adv-key; told adv-key localhost
        envs=HYPERSHELL_SERVER_ADVERTISE=no-such-name.invalid; run adv-both 60 t.in --no-tls --advertise localhost --launcher "$bin/rec.sh"
        expect_ok adv-both; told adv-both localhost
        run adv-local 30 t.in --no-tls --advertise localhost
        [ "$status" = 2 ] || fail "adv-local: status $status, expected 2"
        has adv-local 'advertise.*local cluster' ;;
    *) echo "usage: launch_drive.sh fail|fail-db|zero|healthy|ssh-fail|ssh-partial|autoscale-fail|autoscale-off|advertise" >&2
       exit 2 ;;
esac
echo "launch_drive[$mode]: ok"
