#!/bin/sh
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0
#
# CLI drive for queue-wire-codec: the queue's roles, from the command line, over this tree's queue
# framing, with TLS on and off, in a throwaway site. Run from the worktree root:
#
#   .agents/factory/bin/temp_site.sh sh "$PWD/spec/queue-wire-codec/drive/cli_drive.sh"
#
# temp_site.sh makes the site the working directory and removes it on exit. Every server binds
# 127.0.0.1; the key of `hs server` is in HYPERSHELL_SERVER_AUTH and `hs cluster` makes its own;
# nothing here passes a key on a command line. Each command is bounded, and every process this
# drive starts is stopped before it exits.
#
#   1. `hs cluster` runs 40 tasks with a local client and with a launched client process (TLS on, then
#      --no-tls): every task exits 0, and nothing logs an error or a refused connection.
#   2. `hs server --no-db` with `hs submit --queue` into the live queue, then `hs client` (TLS on, then
#      --no-tls): the client runs every submitted task, every process exits 0, and nothing logs an error
#      or a refusal.
#
# Exits 0 when every check holds, else 1 at the first that does not (its output says which).

set -u

root=${UV_PROJECT:?run under .agents/factory/bin/temp_site.sh from the worktree root}
bounded=$root/spec/submit-input-checks/drive/bounded.py
bin=$(cd "$root" && uv run --quiet python -c 'import os, sys; print(os.path.dirname(sys.executable))') || exit 2
PATH=$bin:$PATH
export PATH
HS=$bin/hs

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
clean_log() {  # FILE: no error, critical or refusal line
    if grep -E 'ERROR|CRITICAL|Traceback|Refused a queue connection|Client too old' "$1" >/dev/null; then
        fail "$1 logged: $(grep -E 'ERROR|CRITICAL|Traceback|Refused|too old' "$1" | head -3)"
    fi
}

seq 40 | sed 's/^/echo /' > tasks.in

for tls in tls no-tls; do
    flag=''
    [ "$tls" = no-tls ] && flag=--no-tls
    for mode in local launched; do
        "$HS" initdb --truncate --yes 2>/dev/null
        port=$(free_port)
        case $mode in
            local) set -- -N 4 ;;
            launched) set -- -N 4 --launcher=env ;;
        esac
        # shellcheck disable=SC2086
        python3 "$bounded" 120 "$HS" cluster tasks.in -H 127.0.0.1 -p "$port" $flag "$@" \
            > "cluster-$tls-$mode.out" 2> "cluster-$tls-$mode.err"
        code=$?
        [ "$code" -eq 0 ] || fail "hs cluster ($tls, $mode) exited $code: $(tail -5 "cluster-$tls-$mode.err")"
        done=$("$HS" list -c -w 'exit_status == 0' 2>/dev/null)
        [ "$done" = 40 ] || fail "hs cluster ($tls, $mode): $done of 40 tasks exited 0"
        clean_log "cluster-$tls-$mode.err"
        pass "hs cluster ($tls, $mode client): 40 of 40 tasks exited 0, no error or refusal logged"
    done

    port=$(free_port)
    HYPERSHELL_SERVER_AUTH=$("$bin/python" -c 'import secrets; print(secrets.token_hex(16))')
    export HYPERSHELL_SERVER_AUTH
    # A server with no source of its own stops its clients once its queue drains, so the queue is deep
    # enough to take every submitted bundle before the client starts.
    # shellcheck disable=SC2086
    HYPERSHELL_SERVER_QUEUESIZE=100 HYPERSHELL_LOGGING_LEVEL=DEBUG \
        "$HS" server --no-db -H 127.0.0.1 -p "$port" $flag < /dev/null 2> "server-$tls.err" &
    server=$!
    started="$started $server"
    n=0
    until grep -q 'Server listening' "server-$tls.err"; do
        n=$((n + 1))
        [ "$n" -lt 100 ] && kill -0 "$server" 2>/dev/null || fail "hs server --no-db ($tls) did not listen: $(tail -5 "server-$tls.err")"
        sleep 0.1
    done
    seq 30 | sed 's/^/echo live-/' > live.in
    # shellcheck disable=SC2086
    python3 "$bounded" 60 "$HS" submit --queue -w 1 -H 127.0.0.1 -p "$port" $flag -f live.in \
        > "submit-$tls.out" 2> "submit-$tls.err"
    code=$?
    [ "$code" -eq 0 ] || fail "hs submit --queue ($tls) exited $code: $(tail -5 "submit-$tls.err")"
    # shellcheck disable=SC2086
    python3 "$bounded" 60 "$HS" client -H 127.0.0.1 -p "$port" $flag -N 2 -T 20 \
        > "client-$tls.out" 2> "client-$tls.err"
    code=$?
    [ "$code" -eq 0 ] || fail "hs client ($tls) exited $code: $(tail -5 "client-$tls.err")"
    ran=$(grep -c '^live-' "client-$tls.out")
    [ "$ran" = 30 ] || fail "hs client ($tls) ran $ran of 30 live tasks"
    n=0
    while kill -0 "$server" 2>/dev/null; do
        n=$((n + 1))
        [ "$n" -lt 300 ] || fail "hs server --no-db ($tls) did not exit after its client"
        sleep 0.1
    done
    wait "$server"
    code=$?
    started=''
    [ "$code" -eq 0 ] || fail "hs server --no-db ($tls) exited $code: $(tail -5 "server-$tls.err")"
    unset HYPERSHELL_SERVER_AUTH
    clean_log "server-$tls.err"
    clean_log "client-$tls.err"
    clean_log "submit-$tls.err"
    pass "hs server --no-db ($tls) with hs client and hs submit --queue: 30 of 30 live tasks ran, nothing refused"
done
echo "cli_drive: every check holds"
