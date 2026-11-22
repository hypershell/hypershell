#!/bin/sh
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0
#
# CLI drive for update-client-retry: runs the commands a user runs, in a throwaway site, and checks
# what they refuse, read and record. Run from the worktree root, one mode at a time:
#
#   .agents/factory/bin/temp_site.sh uv run sh "$PWD/spec/update-client-retry/drive/ucr_drive.sh" MODE
#
# temp_site.sh makes the site the working directory and removes it on exit. Every server binds
# 127.0.0.1; `hs server` takes its key from HYPERSHELL_SERVER_AUTH and `hs cluster` makes its own;
# nothing here passes a key on a command line. Every command ends on its own: the server runs a file
# and stops when it is done, and the client stops when the server does. Exits 0 when every check of
# the mode holds, else 1 at the first that does not (its output says which).
#
#   update    hs update --delete with a change exits 2 before counting, with and without --limit,
#             and deletes nothing; the single-task form with --cancel exits 2 and writes nothing;
#             --delete with filters only still deletes (R1, R2)
#   client    HYPERSHELL_CLIENT_BUNDLESIZE/_BUNDLEWAIT reach hs client and the SUBMIT ones do not;
#             a live client with HYPERSHELL_CLIENT_BUNDLESIZE=3 receives bundles of at most 3 (R3, R4)
#   retry     hs cluster -r 2 with a template that does not expand, and with a task declaring more
#             cores than the node has: one row each, the group halted naming "did not run", the
#             next group never run; a -W 2 task's retry is killed at 2 s too (R5, R6, R7)
#   defaults  run_local from Python retries a failing task per server.attempts: once by default,
#             three attempts with HYPERSHELL_SERVER_ATTEMPTS=3 (R8)

set -u
mode=${1:?usage: ucr_drive.sh update|client|retry|defaults}

fail() { echo "FAIL [$mode] $*"; exit 1; }
pass() { echo "ok   [$mode] $*"; }
count() { hs list -c "$@" 2>/dev/null; }
free_port() { python -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])'; }

case $mode in
update)
    printf 'echo 0  # HYPERSHELL: n:0\necho 1  # HYPERSHELL: n:1\necho 2  # HYPERSHELL: n:2\n' > t.in
    hs submit t.in 2>/dev/null || fail 'submit'
    for args in 'group=9 --delete --limit 1 -f' 'foo:bar --delete -f' 'group=9 --delete -t n:1 -f'; do
        # shellcheck disable=SC2086
        hs update $args 2> err.txt; status=$?
        [ "$status" -eq 2 ] || fail "hs update $args exited $status, not 2: $(cat err.txt)"
        grep -q 'Cannot use --delete with changes' err.txt || fail "message: $(cat err.txt)"
        grep -q 'Searching database' err.txt && fail "counted before refusing: $(cat err.txt)"
        [ "$(count)" = 3 ] || fail "hs update $args deleted a task"
    done
    hs update --delete --limit 1 -f --remove-tag n 2> err.txt; status=$?
    [ "$status" -eq 2 ] || fail "--delete --remove-tag exited $status, not 2"
    [ "$(count)" = 3 ] || fail '--delete --remove-tag deleted a task'
    pass '--delete with a change exits 2, nothing deleted'
    id=$(hs list id -t n:0 2>/dev/null)
    hs update "$id" group 7 --cancel -f 2> err.txt; status=$?
    [ "$status" -eq 2 ] || fail "single-task form with --cancel exited $status, not 2: $(cat err.txt)"
    grep -q -- '--cancel' err.txt || fail "message: $(cat err.txt)"
    [ "$(hs list group -t n:0 2>/dev/null)" = 0 ] || fail 'single-task form wrote the field'
    [ "$(hs list exit_status -t n:0 2>/dev/null)" = null ] || fail 'single-task form cancelled the task'
    pass 'hs update ID group 7 --cancel exits 2, nothing written'
    hs update --delete -t n:1 -f 2>/dev/null || fail '--delete with filters only refused'
    [ "$(count)" = 2 ] && [ "$(count -t n:1)" = 0 ] || fail '--delete -t n:1 did not delete exactly n:1'
    hs update "$id" group 7 2>/dev/null || fail 'single-task form refused'
    [ "$(hs list group -t n:0 2>/dev/null)" = 7 ] || fail 'single-task form did not write'
    pass '--delete with filters only and the plain single-task form work as before'
    ;;
client)
    read_client='from hypershell.client import ClientApp, advertised_size; print(advertised_size(None), ClientApp.interface.parse_args([]).bundlewait)'
    got=$(HYPERSHELL_CLIENT_BUNDLESIZE=7 HYPERSHELL_CLIENT_BUNDLEWAIT=11 python -c "$read_client")
    [ "$got" = '7 11' ] || fail "client keys: advertised and wait are '$got', not '7 11'"
    got=$(HYPERSHELL_SUBMIT_BUNDLESIZE=9 HYPERSHELL_SUBMIT_BUNDLEWAIT=13 python -c "$read_client")
    [ "$got" = 'None 5' ] || fail "submit keys: advertised and wait are '$got', not 'None 5'"
    pass 'hs client reads client.bundlesize/bundlewait, not submit.*'
    seq 60 | sed 's/^/echo /' > t.in
    port=$(free_port)
    HYPERSHELL_SERVER_AUTH=$(python -c 'import secrets; print(secrets.token_hex(16))')
    export HYPERSHELL_SERVER_AUTH
    hs server t.in -b 100 -H 127.0.0.1 -p "$port" --no-tls 2> server.txt &
    server=$!
    sleep 3
    HYPERSHELL_CLIENT_BUNDLESIZE=3 HYPERSHELL_LOGGING_LEVEL=DEBUG \
        hs client -N2 -H 127.0.0.1 -p "$port" --no-tls -T 10 2> client.txt; status=$?
    wait "$server"
    [ "$status" -eq 0 ] || fail "client exited $status: $(tail -5 client.txt)"
    sizes=$(sed -n 's/.*Received \([0-9][0-9]*\) tasks.*/\1/p' client.txt | sort -n | uniq | tr '\n' ' ')
    [ -n "$sizes" ] || fail "no bundles received: $(tail -5 client.txt)"
    for size in $sizes; do [ "$size" -le 3 ] || fail "received a bundle of $size with client.bundlesize 3"; done
    [ "$(count -w 'exit_status == 0')" = 60 ] || fail 'not every task ran'
    pass "a live client with HYPERSHELL_CLIENT_BUNDLESIZE=3 received bundles of $sizes"
    ;;
retry)
    printf 'echo 1  # HYPERSHELL: n:1 group:1\necho 2  # HYPERSHELL: n:2 group:2\n' > t.in
    hs cluster t.in -r 2 -t 'echo {nope}' 2> err.txt || fail "hs cluster (template) exited $?"
    [ "$(count -t n:1)" = 1 ] || fail "template error: n:1 has $(count -t n:1) rows, not 1"
    [ "$(hs list exit_status -t n:1 2>/dev/null)" = -1001 ] || fail 'template error: status is not -1001'
    [ "$(count -t n:2 -w 'exit_status == 0')" = 0 ] || fail 'template error: group 2 ran'
    grep -q 'Failed task group 1 (at least 1 tasks not retried because they did not run' err.txt \
        || fail "template error: halt message: $(grep 'task group' err.txt)"
    grep -q '(cancelled)' err.txt && fail 'template error: the client says (cancelled)'
    grep -q 'Template expansion failed .* (task did not run)' err.txt || fail 'template error: client line'
    pass 'a template that does not expand: one row, group 1 halted, group 2 never run'
    hs initdb --truncate --yes 2>/dev/null || fail 'truncate'
    printf 'echo 3  # HYPERSHELL: n:3 group:1 cores:100000\necho 4  # HYPERSHELL: n:4 group:2\n' > r.in
    hs cluster r.in -r 2 2> err.txt || fail "hs cluster (resources) exited $?"
    [ "$(count -t n:3)" = 1 ] || fail "resources: n:3 has $(count -t n:3) rows, not 1"
    [ "$(hs list exit_status -t n:3 2>/dev/null)" = -1002 ] || fail 'resources: status is not -1002'
    [ "$(count -t n:4 -w 'exit_status == 0')" = 0 ] || fail 'resources: group 2 ran'
    grep -q 'Failed task group 1 (at least 1 tasks not retried because they did not run' err.txt \
        || fail "resources: halt message: $(grep 'task group' err.txt)"
    grep -q 'Insufficient resources .* (task did not run)' err.txt || fail 'resources: client line'
    pass 'a task needing 100000 cores: one row, group 1 halted, group 2 never run'
    hs initdb --truncate --yes 2>/dev/null || fail 'truncate'
    hs submit -c 1 -m 1MB -W 2 sleep 6 2>/dev/null || fail 'submit -W 2'
    start=$(date +%s)
    hs cluster --restart -r 1 -N1 2>/dev/null || fail "hs cluster --restart exited $?"
    elapsed=$(( $(date +%s) - start ))
    [ "$(count)" = 2 ] || fail "walltime: $(count) rows, not 2"
    [ "$(count -w 'exit_status < 0' 'exit_status > -1000')" = 2 ] || fail 'walltime: not both attempts killed'
    [ "$(count -w 'attempt == 2' 'timeout == 2' 'cores == 1')" = 1 ] || fail 'walltime: retry lost -W/-c'
    [ "$elapsed" -lt 12 ] || fail "walltime: the run took $elapsed s; the retry ran past its walltime"
    pass "a -W 2 task is killed at 2 s on its retry too ($elapsed s for both)"
    ;;
defaults)
    # The library does not create a SQLite schema (the commands do), so initdb runs first.
    hs initdb --yes 2>/dev/null || fail 'initdb'
    run="from hypershell.cluster import run_local; run_local(['false'], num_threads=1, port=$(free_port))"
    python -c "$run" 2>/dev/null || fail 'run_local (default) failed'
    [ "$(count)" = 1 ] || fail "default: $(count) rows, not 1 (no retry)"
    hs initdb --truncate --yes 2>/dev/null || fail 'truncate'
    run="from hypershell.cluster import run_local; run_local(['false'], num_threads=1, port=$(free_port))"
    HYPERSHELL_SERVER_ATTEMPTS=3 python -c "$run" 2>/dev/null || fail 'run_local (attempts 3) failed'
    [ "$(count)" = 3 ] || fail "server.attempts 3: $(count) rows, not 3"
    pass 'run_local retries per server.attempts: none by default, two with attempts 3'
    ;;
*)
    echo "ucr_drive.sh: unknown mode $mode" >&2
    exit 2
    ;;
esac
