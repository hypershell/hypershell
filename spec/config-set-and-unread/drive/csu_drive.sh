#!/bin/sh
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0
#
# CLI drive for config-set-and-unread: runs the commands a user runs, in a throwaway site, and checks
# what they write, refuse and do. Run from the worktree root, one mode at a time:
#
#   .agents/factory/bin/temp_site.sh uv run sh "$PWD/spec/config-set-and-unread/drive/csu_drive.sh" MODE
#
# temp_site.sh makes the site the working directory, so `--local` writes `./config.toml` there, and
# removes the site on exit. Every server binds 127.0.0.1; `hs cluster` makes its own key; nothing here
# passes a key on a command line. Exits 0 when every check of the mode holds, else 1 at the first
# that does not (its output says which).
#
#   set    hs config set: a digit key kept as text, a typed value converted, a refusal (exit 2) that
#          leaves the file as it was, an untyped TOML integer, a value that is not UTF-8 refused
#          (exit 2) with the file and its backups as they were (R1, R2, R4)
#   eager  server.eager: a file's "no" runs, an unreadable value stops hs cluster with exit 3 (R5)
#   evict  server.evict under hs cluster: under the floor exits 5 before listening; 1m30s runs (R7)
#   rate   -R and client.ratelimit: a launched client takes -R 0.5 and is throttled; task.ratelimit
#          is not read (R8)

set -u
mode=${1:?usage: csu_drive.sh set|eager|evict|rate}

fail() { echo "FAIL [$mode] $*"; exit 1; }
pass() { echo "ok   [$mode] $*"; }

printf 'echo one\necho two\necho three\n' > t.in

case $mode in
set)
    hs config set server.auth 0012345678901234 --local || fail 'digit key refused'
    grep -qx 'auth = "0012345678901234"' config.toml || fail "digit key not text: $(cat config.toml)"
    [ "$(hs config get -r server.auth)" = 0012345678901234 ] || fail 'digit key read back changed'
    pass 'server.auth 0012345678901234 written and read back as text'
    hs config set server.eager no --local || fail 'eager no refused'
    grep -qx 'eager = false' config.toml || fail "eager no not false: $(cat config.toml)"
    pass 'server.eager no written as false'
    cp config.toml before.toml
    hs config set server.port abc --local 2> refused.err; status=$?
    [ "$status" -eq 2 ] || fail "server.port abc exited $status, not 2"
    grep -q "server.port: expected a whole number, given 'abc'" refused.err || fail "message: $(cat refused.err)"
    cmp -s config.toml before.toml || fail 'refused value changed the file'
    pass 'server.port abc refused with exit 2, file unchanged'
    hs config set database.pragmas.cache_size 007 --local || fail 'pragma refused'
    grep -qx 'cache_size = "007"' config.toml || fail "untyped 007 not text: $(cat config.toml)"
    pass 'untyped 007 kept as text'
    cp config.toml before.toml
    backups=$(ls -a | grep -c '^\.config\.')
    hs config set database.pragmas.x "$(printf 'a\377')" --local 2> refused.err; status=$?
    [ "$status" -eq 2 ] || fail "a value that is not UTF-8 exited $status, not 2: $(cat refused.err)"
    grep -q 'database.pragmas.x: expected text that can be written as UTF-8' refused.err || fail "message: $(cat refused.err)"
    grep -qi traceback refused.err && fail "traceback: $(cat refused.err)"
    cmp -s config.toml before.toml || fail 'a value that is not UTF-8 changed the file'
    [ "$(ls -a | grep -c '^\.config\.')" -eq "$backups" ] || fail 'a value that is not UTF-8 made a backup'
    pass 'a value that is not UTF-8 refused with exit 2, file and backups unchanged'
    ;;
eager)
    printf '[server]\neager = "no"\n' > config.toml
    HYPERSHELL_CONFIG_FILE=$PWD/config.toml hs cluster t.in --no-db > out.txt 2> err.txt \
        || fail "eager \"no\" run failed: $(tail -3 err.txt)"
    [ "$(wc -l < out.txt)" -eq 3 ] || fail "expected 3 task outputs: $(cat out.txt)"
    pass 'eager = "no" runs'
    printf '[server]\neager = "maybe"\n' > config.toml
    HYPERSHELL_CONFIG_FILE=$PWD/config.toml hs cluster t.in --no-db > out.txt 2> err.txt; status=$?
    [ "$status" -eq 3 ] || fail "eager \"maybe\" exited $status, not 3"
    grep -q 'server.eager' err.txt || fail "message: $(cat err.txt)"
    pass 'eager = "maybe" stops hs cluster with exit 3'
    ;;
evict)
    HYPERSHELL_SERVER_EVICT=5 hs cluster t.in --no-db --no-tls -H 127.0.0.1 > out.txt 2> err.txt; status=$?
    [ "$status" -eq 5 ] || fail "evict 5 exited $status, not 5"
    grep -q 'Evict period must be greater than 10 seconds: given 5' err.txt || fail "message: $(cat err.txt)"
    grep -q 'listening' err.txt && fail 'server started listening before the refusal'
    [ -s out.txt ] && fail "tasks ran: $(cat out.txt)"
    pass 'server.evict 5 refused with exit 5 before listening'
    HYPERSHELL_SERVER_EVICT=1m30s hs cluster t.in --no-db --no-tls -H 127.0.0.1 > out.txt 2> err.txt \
        || fail "evict 1m30s run failed: $(tail -3 err.txt)"
    [ "$(wc -l < out.txt)" -eq 3 ] || fail "expected 3 task outputs: $(cat out.txt)"
    pass 'server.evict 1m30s runs'
    ;;
rate)
    start=$(date +%s)
    hs cluster t.in --no-db --no-tls -H 127.0.0.1 --launcher=env -N 1 -R 0.5 > out.txt 2> err.txt \
        || fail "launched -R 0.5 failed: $(tail -5 err.txt)"
    took=$(( $(date +%s) - start ))
    [ "$(wc -l < out.txt)" -eq 3 ] || fail "expected 3 task outputs: $(cat out.txt)"
    [ "$took" -ge 3 ] || fail "3 tasks at 0.5 per second took only ${took}s"
    pass "launched client took -R 0.5 (3 tasks in ${took}s)"
    HYPERSHELL_TASK_RATELIMIT=abc hs cluster t.in --no-db > out.txt 2> err.txt \
        || fail "task.ratelimit was read: $(tail -3 err.txt)"
    [ "$(wc -l < out.txt)" -eq 3 ] || fail "expected 3 task outputs: $(cat out.txt)"
    pass 'task.ratelimit is not read'
    ;;
*)
    echo "usage: csu_drive.sh set|eager|evict|rate" >&2
    exit 2
    ;;
esac
