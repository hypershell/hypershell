#!/bin/sh
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0
#
# End-of-run drives for relay-shutdown-tail: a direct `hs cluster` run in a throwaway site, timed
# from start to exit. Run from the repository root:
#
#   .agents/factory/bin/temp_site.sh sh "$PWD/spec/relay-shutdown-tail/drive/end_drive.sh" MODE
#
# Modes:
#   idle    Three tasks to one client of two threads with a bundle of 8 and a bundle wait of 60 s
#           (-N 2 -b 8 -w 60, -Q 1). The client runs out of work holding three results; it must
#           return them at once, so the run ends in well under the bundle wait. Bound: 15 s.
#   finish  One `sleep 9` task with a bundle of 1 and the default poll (30 s). While it runs the
#           scheduler backs off 1, 2, 4 and then 8 s, so the result lands about 2 s into the 8 s
#           back-off (which ends about 15 s in); the run must end without waiting it out.
#           Bound: 13.5 s (the base build ends past 15 s; the fixed one about 10 s).
#   nodb    The `idle` run with `--no-db` (no database, no scheduler), a guard: it ends promptly on the base too.
#
# Each mode checks the exit status, the wall time, and (with a database) that every task finished
# with status 0 (`hs list`). Exit 0 on pass, 1 on fail, 64 on bad usage. `HS` names the `hs` to run
# (default: `hs` from PATH). Every wait is bounded: the run itself by `bounded.py`.

set -u
here=$(cd "$(dirname "$0")" && pwd)
hs=${HS:-hs}
py=${PY:-python3}
mode=${1:-}

run() {  # BOUND ARGS...: run `hs cluster ARGS` on the tasks on stdin (not a pipe: the caller reads the status); print the wall time; keep the status
    bound=$1; shift
    start=$("$py" -c 'import time; print(time.monotonic())')
    "$py" "$here/bounded.py" "$bound" "$hs" cluster "$@" > out.txt 2> err.txt
    status=$?
    wall=$("$py" -c 'import sys, time; print(f"{time.monotonic() - float(sys.argv[1]):.2f}")' "$start")
    echo "end_drive: $mode: exit $status after $wall s (bound $bound s)"
}

finished() {  # COUNT: every task row finished with status 0
    done=$("$hs" list --succeeded --count 2>/dev/null || echo 0)
    if [ "$done" != "$1" ]; then
        echo "end_drive: $mode: $done of $1 tasks finished with status 0" >&2
        return 1
    fi
}

case $mode in
    idle)
        printf 'true\ntrue\ntrue\n' > tasks.txt; run 15 -N 2 -b 8 -w 60 -Q 1 < tasks.txt
        [ "$status" -eq 0 ] && finished 3 && "$py" -c 'import sys; sys.exit(float(sys.argv[1]) >= 15)' "$wall" ;;
    finish)
        printf 'sleep 9\n' > tasks.txt; run 13.5 -N 1 -b 1 -w 1 < tasks.txt
        [ "$status" -eq 0 ] && finished 1 && "$py" -c 'import sys; sys.exit(float(sys.argv[1]) >= 13.5)' "$wall" ;;
    nodb)
        printf 'true\ntrue\ntrue\n' > tasks.txt; run 15 -N 2 -b 8 -w 60 --no-db < tasks.txt
        [ "$status" -eq 0 ] && "$py" -c 'import sys; sys.exit(float(sys.argv[1]) >= 15)' "$wall" ;;
    *)
        echo "usage: end_drive.sh idle|finish|nodb" >&2
        exit 64 ;;
esac
result=$?
if [ "$result" -eq 0 ]; then echo "end_drive: $mode: PASS"; else echo "end_drive: $mode: FAIL"; tail -n 5 err.txt >&2; fi
exit "$result"
