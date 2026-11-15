#!/bin/sh
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0
#
# Laptop-scale drive of the two task-input fidelity defects: date-like lines that must run as text,
# and a run fed by a stream that must keep scheduling until its input ends. `hs` and `hsx` are taken
# from PATH (run it under `uv run`). Run it in a throwaway site, from the repository root:
#
#   .agents/factory/bin/temp_site.sh uv run sh "$PWD/spec/task-input-fidelity/drive/fidelity_drive.sh" MODE
#
# Modes and checks:
#   dates          hsx FILE -N1 -t 'echo {}': every line printed; `hs list` shows each line's args
#                  unchanged, exit 0 and the command `echo <line>`.
#   dates-default  the same with the default template: each line's command recorded verbatim, exit
#                  127 (no such command), never -1001.
#   dates-stdin    the dates on standard input, with -t 'echo {}'.
#   dates-nodb     the dates with --no-db and -t 'echo {}': every line printed.
#   pause          (echo 'echo a'; sleep FD_PAUSE; echo 'echo b') | hsx -N1: both printed, nothing
#                  remaining, and the run ends within FD_BOUND of the input ending.
#   fail-pause     (echo false; sleep FD_PAUSE; echo 'echo b') | hsx -N1: b still runs after the first
#                  task failed with no retry left; no critical log; one failed task, none remaining.
#   burst          30 lines, a pause of FD_PAUSE seconds, 30 more, into hsx -N4: 60 completed.
#   server         the pause producer into `hs server -`, with a separate `hs client -N1`: both
#                  completed, server and client exit 0.
#   fifo           the pause producer through a named pipe given as FILE: both printed.
#   nodb-stream    the pause producer into hsx -N1 --no-db: both printed (unchanged guard).
#   file           a 4-line task file into hsx -N2: all printed, nothing remaining (unchanged guard).
#   launch-false   hsx --launcher false -N1 -H 127.0.0.1 fed by a pipe that stays open: exits 5
#                  within FD_FAIL seconds (unchanged guard; the pipe is closed afterwards).
#   cross-old-client  this tree's `hs server FILE` with a 2.9.0a8 `hs client` (sources in FD_OLD_SRC);
#   cross-old-server  the reverse: every task completes, `hs list` shows its schedule and completion
#                  times, and the server logs no stale or dropped result.
#
# Database: the site's SQLite file by default. With FD_PROVIDER=postgres or timescale, a database of
# its own is created on the server HS_TEST_POSTGRES names (sandbox off), initialized with `hs initdb`,
# used, and dropped on exit.
# Settings: FD_PAUSE (default 8), FD_POLL (default 30, also exported as server.poll), FD_BOUND (how
# long after the input ends a stream run may take to end; default FD_POLL + 5), FD_FAIL (default 20),
# in seconds. Every process the drive starts is stopped before it exits.
set -eu

mode=${1:?usage: fidelity_drive.sh MODE}
pause=${FD_PAUSE:-8}
poll=${FD_POLL:-30}
fail_secs=${FD_FAIL:-20}
bound=${FD_BOUND:-$(( poll + 5 ))}
site=$PWD
pids=''
dbname=''

fail() { echo "fidelity_drive[$mode]: FAIL: $*" >&2; exit 1; }
note() { echo "fidelity_drive[$mode]: $*"; }

# pg ACTION NAME: create or drop database NAME on the HS_TEST_POSTGRES server.
pg() {
    python3 - "$1" "$2" <<'EOF'
import os, sys
from sqlalchemy import create_engine, text
action, name = sys.argv[1:]
engine = create_engine(os.environ['HS_TEST_POSTGRES'], isolation_level='AUTOCOMMIT')
with engine.connect() as conn:
    conn.execute(text(f'CREATE DATABASE {name}' if action == 'create' else f'DROP DATABASE IF EXISTS {name} WITH (FORCE)'))
EOF
}

cleanup() {
    for pid in $pids; do kill "$pid" 2>/dev/null || true; done
    if [ -n "$dbname" ]; then pg drop "$dbname" || true; fi
}
trap cleanup EXIT INT TERM

if [ -n "${FD_PROVIDER:-}" ]; then
    : "${HS_TEST_POSTGRES:?FD_PROVIDER needs HS_TEST_POSTGRES}"
    dbname="fidelity_$(date +%s)_$$"
    pg create "$dbname"
    eval "$(python3 - <<'EOF'
import os
from sqlalchemy.engine import make_url
url = make_url(os.environ['HS_TEST_POSTGRES'])
for key, value in dict(HOST=url.host, PORT=url.port or 5432, USER=url.username, PASSWORD=url.password).items():
    print(f"export HYPERSHELL_DATABASE_{key}='{value}'")
EOF
)"
    export HYPERSHELL_DATABASE_PROVIDER="$FD_PROVIDER" HYPERSHELL_DATABASE_DATABASE="$dbname"
    unset HYPERSHELL_DATABASE_FILE
    hs initdb --yes > /dev/null
    note "database $dbname ($FD_PROVIDER)"
fi
export HYPERSHELL_SERVER_POLL="$poll"
export HYPERSHELL_SERVER_PORT="$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')"
export HYPERSHELL_SERVER_AUTH="$(python3 -c 'import secrets; print(secrets.token_hex(16))')"

printf '%s\n' 20261102 2026-11-02 2026W45 2026-W45-1 20261102T1200 '2026-11-02 12:00' hello > dates.in

# same_lines FILE...: the files hold the same lines, in any order.
same_lines() { [ "$(sort "$1")" = "$(sort "$2")" ]; }

# check_list TEMPLATE STATUS: every task in the database ran its line through TEMPLATE ('{}' alone
# or 'echo {}') and exited STATUS; nothing remains.
check_list() {
    hs list args exit_status command --json > list.json
    python3 - "$1" "$2" list.json <<'EOF' || fail "hs list: $(cat list.json)"
import json, sys
template, status, path = sys.argv[1], int(sys.argv[2]), sys.argv[3]
rows = json.load(open(path))
bad = [row for row in rows if row['exit_status'] != status or row['command'] != template.replace('{}', row['args'])]
for row in bad:
    print(f'  unexpected: {row}', file=sys.stderr)
sys.exit(1 if bad or len(rows) != 7 else 0)
EOF
    [ "$(hs list --remaining --count)" = 0 ] || fail 'tasks remaining'
}

# producer: two lines with a pause between them.
producer() { echo 'echo a'; sleep "$pause"; echo 'echo b'; }

# check_stream OUT START: OUT holds a and b; the run ended within FD_BOUND of the input ending at
# START + pause.
check_stream() {
    printf 'a\nb\n' > want.txt
    same_lines "$1" want.txt || fail "printed $(tr '\n' ' ' < "$1")"
    took=$(( $(date +%s) - $2 ))
    [ "$took" -le $(( pause + bound )) ] || fail "ended ${took}s after start (input ended at ${pause}s, bound ${bound}s)"
    note "ended ${took}s after start (input ended at ${pause}s)"
}

case $mode in
    dates)
        hsx dates.in -N1 -t 'echo {}' > out.txt
        same_lines out.txt dates.in || fail "printed: $(tr '\n' '|' < out.txt)"
        check_list 'echo {}' 0 ;;
    dates-default)
        hsx dates.in -N1 > out.txt 2> err.txt
        check_list '{}' 127 ;;
    dates-stdin)
        hsx -N1 -t 'echo {}' < dates.in > out.txt
        same_lines out.txt dates.in || fail "printed: $(tr '\n' '|' < out.txt)"
        check_list 'echo {}' 0 ;;
    dates-nodb)
        hsx dates.in -N1 --no-db -t 'echo {}' > out.txt
        same_lines out.txt dates.in || fail "printed: $(tr '\n' '|' < out.txt)" ;;
    pause)
        start=$(date +%s)
        producer | hsx -N1 > out.txt
        check_stream out.txt "$start"
        [ "$(hs list --remaining --count)" = 0 ] || fail 'tasks remaining' ;;
    fail-pause)
        start=$(date +%s)
        { echo false; sleep "$pause"; echo 'echo b'; } | hsx -N1 > out.txt 2> err.txt
        printf 'b\n' > want.txt
        same_lines out.txt want.txt || fail "printed $(tr '\n' ' ' < out.txt)"
        grep -q CRITICAL err.txt && fail "$(grep CRITICAL err.txt)"
        [ "$(hs list --remaining --count)" = 0 ] || fail 'tasks remaining'
        [ "$(hs list --failed --count)" = 1 ] || fail "$(hs list --failed --count) failed, not 1" ;;
    burst)
        { for i in $(seq 1 30); do echo "echo x$i"; done; sleep "$pause"; for i in $(seq 31 60); do echo "echo x$i"; done; } \
            | hsx -N4 > out.txt
        [ "$(wc -l < out.txt | tr -d ' ')" = 60 ] || fail "printed $(wc -l < out.txt | tr -d ' ') lines"
        [ "$(hs list --completed --count)" = 60 ] || fail "$(hs list --completed --count) completed" ;;
    server)
        start=$(date +%s)
        producer | hs server - > server.out 2> server.err &
        server=$!; pids="$pids $server"
        sleep 2
        hs client -N1 > out.txt 2> client.err &
        client=$!; pids="$pids $client"
        wait "$server" || fail "server exited $?"
        wait "$client" || fail "client exited $?"
        check_stream out.txt "$start"
        [ "$(hs list --remaining --count)" = 0 ] || fail 'tasks remaining' ;;
    fifo)
        mkfifo in.fifo
        start=$(date +%s)
        producer > in.fifo &
        pids="$pids $!"
        hsx in.fifo -N1 > out.txt
        check_stream out.txt "$start"
        [ "$(hs list --remaining --count)" = 0 ] || fail 'tasks remaining' ;;
    nodb-stream)
        start=$(date +%s)
        producer | hsx -N1 --no-db > out.txt
        check_stream out.txt "$start" ;;
    file)
        printf 'echo %s\n' 1 2 3 4 > t.in
        hsx t.in -N2 > out.txt
        printf '1\n2\n3\n4\n' > want.txt
        same_lines out.txt want.txt || fail "printed $(tr '\n' ' ' < out.txt)"
        [ "$(hs list --remaining --count)" = 0 ] || fail 'tasks remaining' ;;
    launch-false)
        mkfifo in.fifo
        (echo 'echo a'; exec sleep 600) > in.fifo &
        writer=$!; pids="$pids $writer"
        start=$(date +%s)
        hsx in.fifo --launcher false -N1 -H 127.0.0.1 > out.txt 2> err.txt &
        cluster=$!; pids="$pids $cluster"
        while kill -0 "$cluster" 2>/dev/null && [ $(( $(date +%s) - start )) -lt "$fail_secs" ]; do sleep 1; done
        kill -0 "$cluster" 2>/dev/null && fail "still running after ${fail_secs}s"
        status=0; wait "$cluster" || status=$?
        [ "$status" = 5 ] || fail "exited $status, not 5"
        grep -q 'every launched client has exited' err.txt || fail 'no launch error logged'
        note "exited 5 after $(( $(date +%s) - start ))s with the input still open" ;;
    cross-old-client|cross-old-server)
        # One side runs the 2.9.0a8 sources in FD_OLD_SRC (from `git archive 2.9.0a8 src`), the other this tree.
        old=${FD_OLD_SRC:?set FD_OLD_SRC to an extracted 2.9.0a8 src directory}
        [ -f "$old/hypershell/__init__.py" ] || fail "no hypershell package under $old"
        if [ "$mode" = cross-old-client ]; then server_path=''; client_path=$old; else server_path=$old; client_path=''; fi
        printf 'echo %s\n' 1 2 3 4 5 6 > t.in
        PYTHONPATH=$server_path hs server t.in > server.out 2> server.err &
        server=$!; pids="$pids $server"
        sleep 3
        PYTHONPATH=$client_path hs client -N2 > out.txt 2> client.err &
        client=$!; pids="$pids $client"
        wait "$server" || fail "server exited $?: $(tail -3 server.err)"
        wait "$client" || fail "client exited $?: $(tail -3 client.err)"
        printf '%s\n' 1 2 3 4 5 6 > want.txt
        same_lines out.txt want.txt || fail "printed $(tr '\n' ' ' < out.txt)"
        grep -E 'Refused stale result|Ignored duplicate result' server.err && fail 'a result was dropped'
        hs list schedule_time completion_time exit_status --json > list.json
        python3 - list.json <<'EOF' || fail "hs list: $(cat list.json)"
import json, sys
rows = json.load(open(sys.argv[1]))
sys.exit(0 if len(rows) == 6 and all(r['schedule_time'] and r['completion_time'] and r['exit_status'] == 0 for r in rows) else 1)
EOF
        ;;
    *)
        fail "unknown mode" ;;
esac
note ok
