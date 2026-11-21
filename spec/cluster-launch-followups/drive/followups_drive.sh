#!/bin/sh
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0
#
# Laptop-scale drive of `hs cluster` when its own checks meet a locked SQLite database, and of a
# `--no-db` cluster whose client dies after returning every result. A second connection holds
# `BEGIN EXCLUSIVE` on the site's database (the lock holder below); fake launchers stand in for a
# scheduler: `gated` (every client exits 1, the first only once the lock is held), `false`, `env`
# (runs the client on this host), and a client that returns every result, then exits 1 in place of
# its FINISHED heartbeat (`crashy`). No
# scheduler and no remote host are used. Run it in a throwaway site, from the repository root:
#
#   .agents/factory/bin/temp_site.sh sh "$PWD/spec/cluster-launch-followups/drive/followups_drive.sh" MODE
#
# Modes and checks (no mode may leave an exception traceback file anywhere in the site):
#   locked-scale-fail  --autoscaling fixed -P 2s --launcher gated, the lock held FD_HOLD seconds from
#                      0.5 s after a task is first scheduled: exits 5 within FD_SCALE seconds after
#                      an error naming 3 failed launches in a row, with at least one WARNING that the
#                      database was locked, and no CRITICAL line about it.
#   locked-scale-off   the same with HYPERSHELL_AUTOSCALE_FAILED_LAUNCHES=0: still running at
#                      FD_SCALE seconds, a launch exited after the lock was released, no error.
#   locked-scale-ok    --autoscaling fixed -P 2s --launcher env -T 1s -Q 60s, the lock held FD_HOLD
#                      seconds once every task is complete, its client has left and the scheduler is
#                      backing off 16 s or more (its TRACE log): still serving at FD_SCALE + 20 seconds
#                      with every task's output and none remaining, at least one locked WARNING, no
#                      error. The scheduler must sleep through the lock: the server's own threads
#                      meeting a locked database are out of this drive's scope, and would end the run.
#   locked-launch      --launcher gated, the lock held FD_HOLD seconds from 0.5 s after a task is
#                      first scheduled: exits 5 within 10 s of the release with the error saying
#                      every launched client has exited, after a WARNING that the database was locked.
#   nodb-crashy        --no-db --launcher env --remote-exe crashy: still running at FD_WAIT seconds,
#                      every task's output written, a warning naming the client's status 1, and no
#                      error saying every launched client has exited.
#   nodb-fail          --no-db --launcher false: exits non-zero within FD_FAIL seconds with that error.
#
# Every run binds 127.0.0.1 on a free port: this host's own name need not resolve here.
# Settings: FD_HOLD (default 9), FD_SCALE (default 40), FD_WAIT (default 15), FD_FAIL (default 15).
# Every process the drive starts is stopped by its recorded PID before it exits.
set -eu

mode=${1:?usage: followups_drive.sh MODE}
hold_secs=${FD_HOLD:-9}
scale_secs=${FD_SCALE:-40}
wait_secs=${FD_WAIT:-15}
fail_secs=${FD_FAIL:-15}
site=$PWD
bin=$site/bin
mkdir -p "$bin"
printf 'echo %s\n' 1 2 3 4 > t.in
envs=''  # NAME=value words (no spaces) the next run's command gets, then cleared

# The lock holder: wait (bounded) until a task has been scheduled (`staged`: the scheduler has made
# its start-up reads and waits on demand, reading nothing more while no client asks), or every task
# is complete and the cluster's log LOG says the scheduler is backing off 16 s or more (`idle`); wait
# DELAY more seconds, take BEGIN EXCLUSIVE, create FLAG (the gated launcher's signal), hold the lock
# HOLD seconds, and write the epoch seconds it took and released the lock to OUT. Starting from a
# fixed delay instead would let the lock meet the scheduler's own start-up reads, a Non-goal.
cat > "$bin/hold.py" <<'EOF'
import re, sqlite3, sys, time
path, when, delay, hold, out, log, flag = sys.argv[1:8]
delay, hold = float(delay), float(hold)
query = {'staged': 'SELECT count(schedule_time) > 0 FROM task',
         'idle': 'SELECT count(*) > 0 AND count(completion_time) = count(*) FROM task'}[when]
backoff = re.compile(r'backing off (1[6-9]|[2-9][0-9]) seconds')
def idle():
    with open(log, errors='replace') as stream:
        return any(backoff.search(line) for line in stream)
deadline = time.monotonic() + 60
while time.monotonic() < deadline:
    try:
        reader = sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=0.1)
        try:
            if reader.execute(query).fetchone()[0] and (when != 'idle' or idle()):
                break
        finally:
            reader.close()
    except (sqlite3.Error, OSError):
        pass
    time.sleep(0.1)
else:
    sys.exit(f'hold.py: database never reached {when!r}')
time.sleep(delay)
conn = sqlite3.connect(path, isolation_level=None, timeout=30)
conn.execute('BEGIN EXCLUSIVE')
open(flag, 'w').close()
with open(out, 'a') as stream:
    print(f'held {int(time.time())}', file=stream, flush=True)
time.sleep(hold)
conn.execute('COMMIT')
conn.close()
with open(out, 'a') as stream:
    print(f'released {int(time.time())}', file=stream, flush=True)
EOF

# The crashy client: a real client that returns every result, then exits 1 in place of sending its
# FINISHED heartbeat.
cat > "$bin/crashy.py" <<'EOF'
import os, sys
import hypershell.client as client
def crash(self):
    sys.stderr.write('crashy client: results returned, exiting 1 before FINISHED\n')
    sys.stderr.flush()
    os._exit(1)
client.ClientThread.wait_heartbeat = crash
from hypershell import main
sys.argv[0] = 'hs'
sys.exit(main())
EOF
py=$(uv run --quiet python -c 'import sys; print(sys.executable)')
# The gated launcher: exit 1 once the lock is held (at most 30 s), ignoring the client command.
printf '#!/bin/sh\nn=0\nwhile [ ! -e "%s" ] && [ $n -lt 300 ]; do sleep 0.1; n=$((n + 1)); done\nexit 1\n' \
    "$site/gate.flag" > "$bin/gated"
chmod +x "$bin/gated"
printf '#!/bin/sh\nexec "%s" "%s" "$@"\n' "$py" "$bin/crashy.py" > "$bin/crashy"
chmod +x "$bin/crashy"

fail() { echo "followups_drive[$mode]: $*" >&2; exit 1; }

# stop_tree PID: stop PID and every process under it.
stop_tree() {
    for child in $(ps -axo pid=,ppid= 2>/dev/null | awk -v p="$1" '$2 == p { print $1 }'); do stop_tree "$child"; done
    kill "$1" 2>/dev/null || true
}

# run LABEL SECONDS LOCK ARGS...: run `hs cluster ARGS` (with the words in $envs added to its
# environment) in the background for at most SECONDS. LOCK is `none` or `WHEN:DELAY` for the lock
# holder (FD_HOLD seconds). Sets status (the exit status, or `running` when it was still running and
# has been stopped), elapsed and ended (epoch seconds); output is in LABEL.out and LABEL.err, the
# holder's times in LABEL.lock. Clears $envs.
run() {
    label=$1 secs=$2 lock=$3; shift 3
    : > "$label.lock"
    start=$(date +%s)
    # shellcheck disable=SC2086
    env $envs uv run --quiet hs cluster "$@" > "$label.out" 2> "$label.err" &
    pid=$!; envs=''; hpid=''
    if [ "$lock" != none ]; then
        uv run --quiet python -I "$bin/hold.py" "$HYPERSHELL_DATABASE_FILE" "${lock%%:*}" "${lock#*:}" \
            "$hold_secs" "$site/$label.lock" "$site/$label.err" "$site/gate.flag" 2> "$label.holder" &
        hpid=$!
    fi
    while kill -0 "$pid" 2>/dev/null && [ $(( $(date +%s) - start )) -lt "$secs" ]; do sleep 0.5; done
    ended=$(date +%s); elapsed=$(( ended - start ))
    if kill -0 "$pid" 2>/dev/null; then
        status=running
        stop_tree "$pid"; sleep 1
        kill -9 "$pid" 2>/dev/null || true
        wait "$pid" 2>/dev/null || true
    else
        set +e; wait "$pid"; status=$?; set -e
    fi
    if [ -n "$hpid" ]; then
        # The holder ends on its own within FD_HOLD seconds of taking the lock; never leave it.
        n=0; while kill -0 "$hpid" 2>/dev/null && [ $n -lt $(( hold_secs * 2 + 4 )) ]; do sleep 0.5; n=$((n + 1)); done
        stop_tree "$hpid"; wait "$hpid" 2>/dev/null || true
        grep -q '^released' "$label.lock" || { cat "$label.holder" >&2; fail "$label: the lock was never held and released"; }
    fi
    echo "followups_drive[$label]: $status after ${elapsed}s"
}

released() { awk '$1 == "released" { print $2 }' "$1.lock"; }
expect_exit() {  # expect_exit LABEL: a non-zero exit, not a timeout
    [ "$status" != running ] || { tail -5 "$1.err" >&2; fail "$1: still running after ${elapsed}s"; }
    [ "$status" != 0 ] || fail "$1: exited 0"
}
expect_running() {  # expect_running LABEL: still running at the bound, with no exit error
    [ "$status" = running ] || { tail -5 "$1.err" >&2; fail "$1: ended with $status after ${elapsed}s"; }
    if grep -Eqi -e 'every launched client has exited|launched clients in a row' "$1.err"; then fail "$1: error logged"; fi
}
expect_output() {  # expect_output LABEL: every task's output
    [ "$(sort -n "$1.out" | tr '\n' ' ')" = '1 2 3 4 ' ] || fail "$1: output was $(tr '\n' ' ' < "$1.out")"
}
has() {  # has LABEL PATTERN: LABEL.err matches PATTERN (extended, case-insensitive)
    grep -Eqi -e "$2" "$1.err" || { tail -8 "$1.err" >&2; fail "$1: no '$2' in its log"; }
}
no_traceback() {  # no_traceback LABEL: no exception file, no uncaught exception, no CRITICAL lock line
    tb=$(find "$site" -name 'exception-*.log' | head -n 1)
    if [ -n "$tb" ]; then tail -5 "$tb" >&2; fail "$1: exception traceback file written ($tb)"; fi
    if grep -Eqi -e 'Uncaught exception|Exception traceback written|CRITICAL.*database is locked' "$1.err"; then
        grep -Ei -e 'Uncaught|traceback|CRITICAL' "$1.err" | head -3 >&2; fail "$1: uncaught exception logged"
    fi
}
remaining() { uv run --quiet hs list --remaining --count; }

# A free port, so a drive never meets another server on this host.
port=$(uv run --quiet python -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
common="--no-tls -H 127.0.0.1 -p $port"
locked='database was locked'
case $mode in
    locked-scale-fail)
        run scale-fail "$scale_secs" staged:0.5 t.in $common --autoscaling fixed -P 2s --launcher "$bin/gated"
        no_traceback scale-fail; expect_exit scale-fail
        [ "$status" = 5 ] || fail "scale-fail: status $status, expected 5"
        has scale-fail '3 launched clients in a row'; has scale-fail "WARNING.*$locked" ;;
    locked-scale-off)
        envs=HYPERSHELL_AUTOSCALE_FAILED_LAUNCHES=0
        run scale-off "$scale_secs" staged:0.5 t.in $common --autoscaling fixed -P 2s --launcher "$bin/gated"
        no_traceback scale-off; expect_running scale-off; has scale-off "WARNING.*$locked"
        # A launch exits after the release: the cluster kept relaunching once the lock was gone.
        [ "$(grep -ci 'exited with status' scale-off.err)" -ge 4 ] || fail "scale-off: not relaunching" ;;
    locked-scale-ok)
        envs=HYPERSHELL_LOGGING_LEVEL=TRACE
        run scale-ok $(( scale_secs + 20 )) idle:0 t.in $common -Q 60s -T 1s --autoscaling fixed -P 2s --launcher env
        no_traceback scale-ok; expect_running scale-ok; expect_output scale-ok
        has scale-ok "WARNING.*$locked"
        [ "$(remaining)" = 0 ] || fail "scale-ok: tasks remain" ;;
    locked-launch)
        run launch "$scale_secs" staged:0.5 t.in $common --launcher "$bin/gated"
        no_traceback launch; expect_exit launch
        [ "$status" = 5 ] || fail "launch: status $status, expected 5"
        has launch 'every launched client has exited'; has launch "WARNING.*$locked"
        rel=$(released launch); [ -n "$rel" ] || fail "launch: no release time"
        [ $(( ended - rel )) -le 10 ] || fail "launch: ended $(( ended - rel ))s after the release" ;;
    nodb-crashy)
        run crashy "$wait_secs" none t.in --no-db $common --launcher env --remote-exe "$bin/crashy"
        no_traceback crashy; expect_running crashy; expect_output crashy; has crashy 'status 1' ;;
    nodb-fail)
        run nodb-fail "$fail_secs" none t.in --no-db $common --launcher false
        no_traceback nodb-fail; expect_exit nodb-fail
        [ "$status" = 5 ] || fail "nodb-fail: status $status, expected 5"
        has nodb-fail 'every launched client has exited' ;;
    *) echo "usage: followups_drive.sh locked-scale-fail|locked-scale-off|locked-scale-ok|locked-launch|nodb-crashy|nodb-fail" >&2
       exit 2 ;;
esac
echo "followups_drive[$mode]: ok"
