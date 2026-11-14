#!/bin/sh
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0
#
# Laptop-scale drive of the relay-hardening fixes: one upstream `hs server` and one `hs relay`, each
# a separate process on loopback, the relay with its own relay database, site and port, launching
# its site clients through hardening_shim.sh, which records (and may hold) each launch. Built on the
# relay-retire drive's machinery, which stays as that cycle's record. Run it in a throwaway site,
# from the repository root:
#
#   .agents/factory/bin/temp_site.sh sh "$PWD/spec/relay-hardening/drive/hardening_drive.sh" MODE
#
# Every process logs in the system style, in UTC, at DEBUG. Each task appends
# `$TASK_ID EPOCH ZONE` to runs.log when it starts (ZONE is the site client's HYPERSHELL_ZONE),
# then sleeps. hardening_judge.py judges the run from the drive's files and the upstream database.
#
# Modes:
#   restart      `hs server --restart` with a database and a backlog of 60 tasks of 1 s, one relay
#                with 3 site clients. The upstream server is stopped (SIGTERM) and started again on
#                its port twice: once at once when 15 tasks are complete, and once, down for 3 s,
#                when 35 are, the relay then holding results not yet returned (its bundlewait is
#                1 s). Pass: the relay never exits 5 and logs no traceback, every task completes, and
#                the relay and the last upstream server exit 0.
#   tls-on       the relay with TLS against an upstream server with --no-tls.
#   tls-off      the relay with --no-tls against an upstream server with TLS. Pass, both: the relay
#                exits 5 within relay.upstream.deadline (30 s) of its start, with one CRITICAL line
#                that names its own setting and says the two ends must agree on TLS, no traceback,
#                no relay-local server and no launch; the upstream server is still running.
#   zones        a relay serving zones cpu (2 clients) and gpu (1 client) against `hs server
#                --restart` with a database. Group 0: 12 cpu tasks of 2 s and 2 gpu tasks of 1 s;
#                group 1: 4 cpu tasks of 1 s and 3 gpu tasks of 1 s. The gpu client runs group 0's
#                gpu tasks and exits idle (site client timeout 2 s); the relay launches it again,
#                and the shim holds that launch. Group 1's gpu tasks are then staged upstream for
#                the relay's gpu identity, which still beats with no site client. Once every task
#                is staged the upstream server is drained (SIGUSR1): it serves each zone what is
#                staged for it, then tells it to shut down. The held gpu client comes back once the
#                relay holds a gpu task taken at the shutdown (or, failing that, 20 s after the cpu
#                zone heard the shutdown, or at the relay's exit). Pass: the relay held a gpu task
#                at the shutdown before the gpu client came back, every task ran once at the site in
#                its own zone, and the relay and the upstream server exit 0.
#   zones-nodb   a relay serving the default zone (2 clients) and gpu against `hs server --no-db`
#                with 8 tasks of 1 s (without a database a task has no zone). Every gpu launch is
#                held for the whole run, so gpu never has a site client and nothing is staged for
#                it. Pass: every task ran once at the site in the default zone, the gpu zone ended
#                with the default zone's shutdown, and the relay and the upstream server exit 0.
#   pair-relay   the relay on the earlier build (DRIVE_BASE_REV, default 2ffb5cc), the server on
#                this one; 40 tasks of 0.2 s, 2 site clients.
#   pair-server  the server on the earlier build, the relay on this one, as pair-relay. Pass, both:
#                every task ran once at the site and completed, and both exit 0.
#
# Settings: DRIVE_DEADLINE, seconds each wait is bounded by (default 180). DRIVE_EVIDENCE, a
# directory: when set, the launches, the server's and relay's relevant lines, the facts and the
# judgment are written to MODE.txt there, with host, user, paths and the run key replaced.
# DRIVE_BASE_REV, the earlier build (default 2ffb5cc); DRIVE_BASE_SRC, an exported `src` of it to
# use instead of exporting one; DRIVE_BASE_ROLES, the roles (relay, server or both) to run on it in
# place of the mode's own, to show a mode failing on the earlier build. The script deletes nothing; it prints its scratch path and stops
# every process it started, by its recorded pid, on failure too.
set -eu

mode=${1:-restart}
here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../../.." && pwd)
deadline=${DRIVE_DEADLINE:-180}
work=$PWD
log=$work/launches.log
runs=$work/runs.log
facts=$work/facts.txt
: > "$log"
: > "$runs"
: > "$facts"
TZ=UTC
export TZ
echo "hardening_drive ($mode): scratch $work"

case $mode in
    restart|tls-on|tls-off|zones|zones-nodb|pair-relay|pair-server) ;;
    *) echo "usage: hardening_drive.sh restart|tls-on|tls-off|zones|zones-nodb|pair-relay|pair-server" >&2; exit 2 ;;
esac

HS=$(uv run --quiet python -c 'import os, sys; print(os.path.join(os.path.dirname(sys.executable), "hs"))')
PY=$(dirname "$HS")/python
PATH=$(dirname "$HS"):$PATH  # Site clients are launched as `hyper-shell client`.
export PATH
free_port() { "$PY" -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])'; }
port=$(free_port)
key="hardening-drive-$("$PY" -c 'import secrets; print(secrets.token_hex(16))')"
fact() { echo "$1=$2" >> "$facts"; }
fact mode "$mode"

# The roles on the earlier build, if any: their PYTHONPATH is the exported source.
base_roles=
case $mode in
    pair-relay) base_roles=relay ;;
    pair-server) base_roles=server ;;
esac
base_roles=${DRIVE_BASE_ROLES:-$base_roles}
base_src=
if [ -n "$base_roles" ]; then
    base_src=${DRIVE_BASE_SRC:-}
    if [ -z "$base_src" ]; then
        mkdir -p "$work/base"
        git -C "$root" archive "${DRIVE_BASE_REV:-2ffb5cc}" src | tar -x -C "$work/base"
        base_src=$work/base/src
    fi
fi
pythonpath() {  # ROLE: the PYTHONPATH that role runs with (empty: this build)
    for role in $base_roles; do
        if [ "$role" = "$1" ]; then echo "$base_src"; return; fi
    done
    echo ''
}
source_of() {  # ROLE
    env PYTHONPATH="$(pythonpath "$1")" "$PY" -c 'import os, hypershell; print(os.path.dirname(hypershell.__file__))'
}
for role in server relay; do fact "source_$role" "$(source_of "$role")"; done

# Every process this script starts, by pid: the servers and the relay. Site clients are the relay's;
# any still running at the end are stopped too (their pids are in the launch log).
started=
ended=
stop_pid() {  # PID: SIGTERM, then SIGKILL if it has not ended within 15 s
    kill -TERM "$1" 2>/dev/null || return 0
    n=0
    while kill -0 "$1" 2>/dev/null && [ "$n" -lt 15 ]; do sleep 1; n=$((n + 1)); done
    kill -KILL "$1" 2>/dev/null || true
}
launched_pids() { sed -n 's/^launch .* pid=\([0-9]*\) .*/\1/p' "$log"; }
cleanup() {
    [ -n "$ended" ] && return
    ended=1
    for pid in $started; do kill -TERM "$pid" 2>/dev/null || true; done
    for pid in $(launched_pids); do kill -TERM "$pid" 2>/dev/null || true; done
    sleep 2
    for pid in $started $(launched_pids); do
        if kill -0 "$pid" 2>/dev/null; then stop_pid "$pid"; fi
    done
}
trap cleanup EXIT
trap 'exit 130' INT TERM

task_line() {  # SECONDS
    echo "echo \"\$TASK_ID \$(date +%s) \${HYPERSHELL_ZONE:--}\" >> $runs; sleep $1"
}
tasks() {  # COUNT SECONDS > FILE
    n=1
    while [ "$n" -le "$1" ]; do task_line "$2"; n=$((n + 1)); done
}
submit() {  # COUNT SECONDS GROUP [ZONE]: from a file of its own (a file is submitted once)
    file=tasks-$3-${4:-default}.in
    tasks "$1" "$2" > "$file"
    if [ -n "${4:-}" ]; then
        "$HS" submit -g "$3" --zone "$4" -f "$file" 2>/dev/null
    else
        "$HS" submit -g "$3" -f "$file" 2>/dev/null
    fi
}

# --- The upstream server ---------------------------------------------------------------------------
servers=0
start_server() {  # ARGS...: one more upstream server, logging to server-N.log; $server is its pid
    servers=$((servers + 1))
    env PYTHONPATH="$(pythonpath server)" HYPERSHELL_SERVER_AUTH="$key" \
        HYPERSHELL_LOGGING_LEVEL=DEBUG HYPERSHELL_LOGGING_STYLE=system \
        "$HS" server -H 127.0.0.1 -p "$port" "$@" 2> "$work/server-$servers.log" &
    server=$!
    started="$started $server"
    fact "server_${servers}_started_at" "$(date +%s)"
}
listening() { "$PY" -c 'import socket, sys; socket.create_connection(("127.0.0.1", int(sys.argv[1])), timeout=1).close()' "$port" 2>/dev/null; }

# --- The relay -------------------------------------------------------------------------------------
# Each zone launches through the shim: ZONES is "NAME:SIZE:HOLD ..." (HOLD as in hardening_shim.sh).
relay_dir=$work/relay
mkdir -p "$relay_dir"
start_relay() {  # ZONES [ARGS...]
    zones=$1
    shift
    zone_args=
    for spec in $zones; do zone_args="$zone_args --zone ${spec%%:*}"; done
    {
        printf '[relay]\nsite = "drive"\ntimeout = 2\n'
        for spec in $zones; do
            name=${spec%%:*}
            rest=${spec#*:}
            printf '[relay.zone.%s]\nsize = %s\nlauncher = "sh %s %s relay %s %s"\n' \
                "$name" "${rest%%:*}" "$here/hardening_shim.sh" "$log" "$name" "${rest#*:}"
        done
    } > "$relay_dir/relay.toml"
    fact relay_started_at "$("$PY" -c 'import time; print(f"{time.time():.3f}")')"
    env -u HYPERSHELL_DATABASE_FILE PYTHONPATH="$(pythonpath relay)" \
        HYPERSHELL_SITE="$relay_dir" HYPERSHELL_CONFIG_FILE="$relay_dir/relay.toml" \
        HYPERSHELL_RELAY_DATABASE="$relay_dir/relay.db" \
        HYPERSHELL_RELAY_UPSTREAM_AUTH="$key" HYPERSHELL_RELAY_BUNDLEWAIT=1 \
        HYPERSHELL_RELAY_UPSTREAM_HEARTRATE=1 HYPERSHELL_SERVER_TLS_ENABLED=false HYPERSHELL_AUTOSCALE_PERIOD=1 \
        HYPERSHELL_LOGGING_LEVEL=DEBUG HYPERSHELL_LOGGING_STYLE=system \
        "$HS" relay -H 127.0.0.1 -p "$port" $zone_args --bind 127.0.0.1 --port "$(free_port)" "$@" \
        2> "$relay_dir/relay.log" &
    relay=$!
    started="$started $relay"
}
running() { kill -0 "$1" 2>/dev/null; }
wait_for() {  # WHAT COMMAND...
    what=$1
    shift
    n=0
    until "$@"; do
        n=$((n + 1))
        if [ "$n" -ge "$deadline" ]; then echo "hardening_drive: timed out waiting for $what" >&2; return 1; fi
        sleep 1
    done
}
exited() {  # NAME PID: wait for its exit (bounded), record its status as fact NAME_exit (-1: stopped)
    n=0
    while running "$2" && [ "$n" -lt "$deadline" ]; do sleep 1; n=$((n + 1)); done
    if running "$2"; then
        fact "$1_exit" -1
        stop_pid "$2"
    elif wait "$2"; then
        fact "$1_exit" 0
    else
        fact "$1_exit" "$?"
    fi
    fact "$1_exited_at" "$("$PY" -c 'import time; print(f"{time.time():.3f}")')"
}
completed() { "$HS" list --completed --count 2>/dev/null || echo 0; }
unscheduled_none() {  # Every upstream task has been scheduled (read-only)
    "$PY" -c '
import sys, sqlite3
conn = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True, timeout=1)
sys.exit(0 if conn.execute("SELECT count(*) FROM task WHERE schedule_time IS NULL").fetchone()[0] == 0 else 1)' \
        "$HYPERSHELL_DATABASE_FILE" 2>/dev/null
}
relay_logged() { grep -q -e "$1" "$relay_dir/relay.log" 2>/dev/null; }

case $mode in

    restart)
        submit 60 1 0
        fact total "$("$HS" list --count)"
        start_server --restart --no-tls
        wait_for 'the upstream server' listening
        start_relay 'default:3:-1' --no-tls
        for step in 1 2; do
            if [ "$step" = 1 ]; then at=15 down=0; else at=35 down=3; fi
            wait_for "$at completed tasks" sh -c "[ \"\$('$HS' list --completed --count 2>/dev/null || echo 0)\" -ge $at ]"
            fact "restart_${step}_at" "$(date +%s)"
            fact "restart_${step}_completed" "$(completed)"
            stop_pid "$server"
            wait "$server" 2>/dev/null || true
            sleep "$down"
            start_server --restart --no-tls
            wait_for 'the restarted upstream server' listening
        done
        exited server "$server"
        exited relay "$relay"
        ;;

    tls-on|tls-off)
        if [ "$mode" = tls-on ]; then
            start_server --forever --no-tls
            relay_args=
        else
            start_server --forever
            relay_args=--no-tls
        fi
        wait_for 'the upstream server' listening
        start_relay 'default:2:-1' $relay_args
        exited relay "$relay"
        fact server_running "$(running "$server" && echo 1 || echo 0)"
        stop_pid "$server"
        ;;

    zones)
        submit 12 2 0 cpu
        submit 2 1 0 gpu
        submit 4 1 1 cpu
        submit 3 1 1 gpu
        fact total "$("$HS" list --count)"
        start_server --restart --no-tls
        wait_for 'the upstream server' listening
        start_relay 'cpu:2:-1 gpu:1:1' --no-tls
        wait_for 'every task staged upstream' unscheduled_none
        fact drained_at "$(date +%s)"
        kill -USR1 "$server"
        # The gpu client comes back once the relay holds a gpu task it took at the shutdown, or 20 s
        # after the cpu zone heard the shutdown, or once the relay has exited.
        wait_for 'the cpu zone to hear the shutdown' relay_logged 'no more requests for zone cpu'
        fact cpu_shutdown_seen_at "$(date +%s)"
        n=0
        until relay_logged 'for zone gpu, which has no site client' || ! running "$relay" || [ "$n" -ge 20 ]; do
            sleep 1
            n=$((n + 1))
        done
        fact released_at "$("$PY" -c 'import time; print(f"{time.time():.3f}")')"
        touch "$work/release-gpu"
        exited server "$server"
        exited relay "$relay"
        ;;

    zones-nodb)
        tasks 8 1 > tasks.in
        fact total 8
        start_server --no-db --no-tls tasks.in
        wait_for 'the upstream server' listening
        start_relay 'default:2:-1 gpu:1:0' --no-tls
        exited server "$server"
        exited relay "$relay"
        ;;

    pair-relay|pair-server)
        submit 40 0.2 0
        fact total "$("$HS" list --count)"
        start_server --restart --no-tls
        wait_for 'the upstream server' listening
        start_relay 'default:2:-1' --no-tls
        exited server "$server"
        exited relay "$relay"
        ;;
esac
fact servers "$servers"

# --- Judgment and report --------------------------------------------------------------------------
if "$PY" "$here/hardening_judge.py" judge "$work" "$mode" > "$work/judgment.txt"; then status=0; else status=$?; fi
relevant_lines() {
    for file in "$work"/server-*.log; do
        echo "# $(basename "$file"): start, restarts seen, shutdown, exit"
        grep -E 'Started|Reverted|Requeued|Signal interrupt|Signaling clients|Evicting client|CRITICAL|ERROR|Traceback|Done$' "$file" | head -n 40 || true
    done
    echo "# relay: identity, link, TLS, zones at the shutdown, site clients, final beat, exit"
    grep -E 'Upstream identity|Lost connection|onnected to upstream|restarted|TLS|must agree|CRITICAL|ERROR|Traceback|shutting down|Relay-local server ready|Ended [0-9]+ site clients|Upstream heartbeat - final' \
        "$relay_dir/relay.log" | head -n 80 || true
}
echo '--- launches'
cat "$log"
echo '--- facts'
cat "$facts"
echo '--- judgment'
cat "$work/judgment.txt"
result=$(tail -n 1 "$work/judgment.txt")
if [ -n "${DRIVE_EVIDENCE:-}" ]; then
    mkdir -p "$DRIVE_EVIDENCE"
    host=$(hostname)
    short=$(hostname -s)
    user=${USER:-$(id -un)}
    {
        echo "# hardening_drive $mode: launches (epoch, label, pid, zone, launch number, HYPERSHELL_ZONE, client --zone)"
        cat "$log"
        relevant_lines
        echo "# hardening_drive $mode: facts"
        cat "$facts"
        echo "# hardening_drive $mode: judgment"
        cat "$work/judgment.txt"
    } | sed -e "s|$root|<repo>|g" -e "s|$work|<site>|g" -e "s|$HOME|<home>|g" -e "s|$host|<host>|g" \
            -e "s|$short|<host>|g" -e "s|$user|<user>|g" -e "s|$key|<key>|g" \
        > "$DRIVE_EVIDENCE/$mode.txt"
fi
echo "hardening_drive ($mode): $result"
exit "$status"
