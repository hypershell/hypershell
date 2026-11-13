#!/bin/sh
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0
#
# Laptop-scale drive of a superseded relay's retirement. One `hs server --autoscaling=fixed` with
# no launcher, and relays as separate processes on loopback, each with its own relay database and
# port, launching their site clients through retire_shim.sh, which records each launch. Run it in a
# throwaway site, from the repository root:
#
#   .agents/factory/bin/temp_site.sh sh "$PWD/spec/relay-retire/drive/retire_drive.sh" MODE
#
# Every process logs in the system style, in UTC. The relays beat every second (so three heartbeat
# intervals are 3 s), the server checks every second, and site clients exit after 3 idle seconds.
# Each task appends `$TASK_ID EPOCH` to runs.log when it starts, then sleeps. A read-only sampler
# (retire_sampler.py) records the upstream task table every 0.25 s, and the drive records each
# relay's running site clients every second; retire_judge.py judges the run from these files.
#
# The scenario (modes retire, retire-red, pair-relay, pair-server), RV5 of the relay-topology
# review: relay A at site A (cpu init and max 3) runs 12 tasks of 10 s. Its site clients take what
# they can hold, find nothing more for 3 s and pass their idle timeout while their tasks still run,
# and A keeps pulling for them: what it pulls then sits at A with no client to take it (the fixed
# policy launches no client while one runs). Once A has run 3 clients and has held a task no client
# took for 2 s running, a second relay A2 starts at the same site (cpu init and max 2), as a relay
# restarted while the old process still runs. The server holds the site for A2 and marks A retired.
#
# Modes:
#   retire       every role on this build. A retires: it hands back what no client started, its
#                clients finish, and it exits 0 by itself, with no eviction (eviction after 120 s,
#                longer than the run, so eviction cannot rescue it); the tasks it held unstarted
#                are available upstream within 3 s of its first "Superseded" line (R7).
#   retire-red   every role on the earlier build (DRIVE_BASE_REV, default fb89670, exported with
#                `git archive` and put on PYTHONPATH). Passes only if a task stays on A's identity
#                for more than 20 s while A runs no site client: the stranding this cycle fixes.
#   pair-relay   relay A on the earlier build, the server and A2 on this one: no topology read
#                error, and A strands as today until it is stopped (SIGTERM) and evicted (after
#                20 s in this mode); every task then completes.
#   pair-server  the server on the earlier build, the relays on this one: no relay retires, no
#                hand-back is made, and A strands as today until stopped and evicted.
#   idle         every role on this build; relays A and B at sites A and B (cpu init and max 2) run 6
#                tasks of 1 s; after the work every client exits by itself and both relays stay
#                connected at zero clients (no relay retires) until drained, then exit 0.
#
# Settings: DRIVE_DEADLINE, seconds each wait is bounded by (default 180). DRIVE_EVIDENCE, a
# directory: when set, the launches, the server's and relays' relevant lines and the judgment are
# written to MODE.txt there, with host, user and paths replaced. DRIVE_BASE_REV, the earlier build
# (default fb89670); DRIVE_BASE_SRC, an exported `src` of it to use instead of exporting one. The
# script deletes nothing; it prints its scratch path and stops every process it started, by its
# recorded pid, on failure too.
set -eu

mode=${1:-retire}
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
: > "$work/alive.log"
TZ=UTC
export TZ
echo "retire_drive ($mode): scratch $work"

case $mode in
    retire|retire-red|pair-relay|pair-server|idle) ;;
    *) echo "usage: retire_drive.sh retire|retire-red|pair-relay|pair-server|idle" >&2; exit 2 ;;
esac

HS=$(uv run --quiet python -c 'import os, sys; print(os.path.join(os.path.dirname(sys.executable), "hs"))')
PY=$(dirname "$HS")/python
PATH=$(dirname "$HS"):$PATH  # Site clients are launched as `hyper-shell client`.
export PATH
free_port() { "$PY" -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])'; }
port=$(free_port)
key="retire-drive-$("$PY" -c 'import secrets; print(secrets.token_hex(16))')"
fact() { echo "$1=$2" >> "$facts"; }

# The roles on the earlier build, if any: their PYTHONPATH is the exported source.
base_roles=
case $mode in
    retire-red) base_roles='server A A2' ;;
    pair-relay) base_roles='A' ;;
    pair-server) base_roles='server' ;;
esac
base_src=
if [ -n "$base_roles" ]; then
    base_src=${DRIVE_BASE_SRC:-}
    if [ -z "$base_src" ]; then
        mkdir -p "$work/base"
        git -C "$root" archive "${DRIVE_BASE_REV:-fb89670}" src | tar -x -C "$work/base"
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
for role in server A A2 B; do fact "source_$role" "$(source_of "$role")"; done

# Every process this script starts, by pid: the server, the relays and the sampler. Site clients
# are the relays'; any still running at the end are stopped too (their pids are in the launch log).
started=
ended=
stop_pid() {  # PID: SIGTERM, then SIGKILL if it has not ended within 15 s
    kill -TERM "$1" 2>/dev/null || return 0
    n=0
    while kill -0 "$1" 2>/dev/null && [ "$n" -lt 15 ]; do sleep 1; n=$((n + 1)); done
    kill -KILL "$1" 2>/dev/null || true
}
cleanup() {
    [ -n "$ended" ] && return
    ended=1
    for pid in $started; do kill -TERM "$pid" 2>/dev/null || true; done
    for pid in $(sed -n 's/.* pid=\([0-9]*\) .*/\1/p' "$log"); do kill -TERM "$pid" 2>/dev/null || true; done
    sleep 2
    for pid in $started $(sed -n 's/.* pid=\([0-9]*\) .*/\1/p' "$log"); do
        if kill -0 "$pid" 2>/dev/null; then stop_pid "$pid"; fi
    done
}
trap cleanup EXIT
trap 'exit 130' INT TERM

# Tasks: COUNT SECONDS. Each records its own start.
submit() {
    n=1
    while [ "$n" -le "$1" ]; do
        echo "echo \"\$TASK_ID \$(date +%s)\" >> $runs; sleep $2"
        n=$((n + 1))
    done > tasks.in
    "$HS" submit -g 0 --zone cpu -f tasks.in 2>/dev/null
}

# Relays: NAME SITE MAX
start_relay() {  # NAME SITE SIZE (the zone's init and max)
    dir=$work/relay-$1
    mkdir -p "$dir"
    printf '[relay]\nsite = "%s"\ntimeout = 3\nlauncher = "sh %s %s %s {zone}"\n[relay.zone.cpu]\ninit = %s\nmax = %s\n' \
        "$2" "$here/retire_shim.sh" "$log" "$1" "$3" "$3" > "$dir/relay.toml"
    env -u HYPERSHELL_DATABASE_FILE PYTHONPATH="$(pythonpath "$1")" \
        HYPERSHELL_SITE="$dir" HYPERSHELL_CONFIG_FILE="$dir/relay.toml" \
        HYPERSHELL_RELAY_DATABASE="$dir/relay.db" \
        HYPERSHELL_RELAY_UPSTREAM_HOST=127.0.0.1 HYPERSHELL_RELAY_UPSTREAM_PORT="$port" \
        HYPERSHELL_RELAY_UPSTREAM_AUTH="$key" HYPERSHELL_RELAY_UPSTREAM_TLS_ENABLED=false \
        HYPERSHELL_RELAY_UPSTREAM_HEARTRATE=1 HYPERSHELL_SERVER_TLS_ENABLED=false \
        HYPERSHELL_AUTOSCALE_PERIOD=1 HYPERSHELL_LOGGING_LEVEL=DEBUG HYPERSHELL_LOGGING_STYLE=system \
        "$HS" relay --zone cpu --bind 127.0.0.1 --port "$(free_port)" 2> "$dir/relay.log" &
    eval "relay_$1=$!"
    started="$started $!"
}

alive_count() {  # LABEL: site clients launched by that relay and still running
    n=0
    while read -r _ _ label pid _; do
        if [ "${label#site=}" = "$1" ] && kill -0 "${pid#pid=}" 2>/dev/null; then n=$((n + 1)); fi
    done < "$log"
    echo "$n"
}
remaining() { "$HS" list --remaining --count 2>/dev/null || echo '?'; }
running() { eval "pid=\${relay_$1:-}"; [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; }
identity_logged() { grep -q 'Upstream identity' "$work/relay-$1/relay.log" 2>/dev/null; }
undelivered() {  # NAME: tasks held at that relay, incomplete, that no site client has taken (read-only)
    "$PY" -c '
import sys, sqlite3
try:
    conn = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True, timeout=1)
    print(conn.execute("SELECT count(*) FROM task WHERE completion_time IS NULL AND client_id IS NULL").fetchone()[0])
except sqlite3.Error:
    print(0)' "$work/relay-$1/relay.db"
}

case $mode in
    idle) submit 6 1; evict=120 ;;
    pair-relay|pair-server) submit 12 10; evict=20 ;;
    *) submit 12 10; evict=120 ;;
esac
total=$("$HS" list --count)
fact total "$total"
fact heartrate 1

env PYTHONPATH="$(pythonpath server)" HYPERSHELL_SERVER_AUTH="$key" HYPERSHELL_SERVER_EVICT="$evict" \
    HYPERSHELL_LOGGING_LEVEL=DEBUG HYPERSHELL_LOGGING_STYLE=system \
    "$HS" server --autoscaling=fixed -P 1 --no-tls -H 127.0.0.1 -p "$port" 2> server.log &
server=$!
started="$started $server"

wait_for() {  # WHAT COMMAND...
    what=$1
    shift
    n=0
    until "$@"; do
        n=$((n + 1))
        if [ "$n" -ge "$deadline" ]; then echo "retire_drive: timed out waiting for $what" >&2; exit 1; fi
        sleep 1
    done
}

if [ "$mode" = idle ]; then
    start_relay A A 2
    start_relay B B 2
else
    start_relay A A 3
fi
wait_for "relay A's identity" identity_logged A
"$PY" "$here/retire_sampler.py" "$HYPERSHELL_DATABASE_FILE" "$work/held.jsonl" &
sampler=$!
started="$started $sampler"

# --- Idle: the work, then nothing ------------------------------------------------------------------
if [ "$mode" = idle ]; then
    wait_for 'the work' sh -c "[ \"\$('$HS' list --remaining --count 2>/dev/null)\" = 0 ]"
    clients_left=0
    n=0
    while [ "$(alive_count A)" != 0 ] || [ "$(alive_count B)" != 0 ]; do
        n=$((n + 1))
        if [ "$n" -ge "$deadline" ]; then clients_left=1; break; fi
        sleep 1
    done
    fact clients_left "$([ "$clients_left" = 0 ] && echo 1 || echo 0)"
    sleep 6  # The relays stay connected with no client.
    relays_running=1
    running A || relays_running=0
    running B || relays_running=0
    fact relays_running "$relays_running"
    fact drained_at "$(date +%s)"
    kill -USR1 "$relay_A" "$relay_B" 2>/dev/null || true
    drained=0
    for pid in $relay_A $relay_B; do
        n=0
        while kill -0 "$pid" 2>/dev/null && [ "$n" -lt 60 ]; do sleep 1; n=$((n + 1)); done
        if kill -0 "$pid" 2>/dev/null; then drained=1; stop_pid "$pid"; else wait "$pid" || drained=1; fi
    done
    fact drained "$drained"
fi

# --- The superseded relay ------------------------------------------------------------------------------
stranded_at= a_done=
if [ "$mode" != idle ]; then
    waited=0 full= held_for=0
    while :; do
        now=$(date +%s)
        a_alive=$(alive_count A)
        echo "$now $a_alive $(alive_count A2)" >> "$work/alive.log"
        if [ -z "${relay_A2:-}" ]; then
            [ "$a_alive" -ge 3 ] && full=1
            undelivered_now=$(undelivered A)
            echo "$now undelivered at A: $undelivered_now" >> "$work/trigger.log"
            if [ -n "$full" ] && [ "$undelivered_now" -gt 0 ]; then held_for=$((held_for + 1)); else held_for=0; fi
            if [ "$held_for" -ge 2 ]; then
                start_relay A2 A 2
                fact a2_started_at "$now"
            fi
        fi
        if [ -z "$a_done" ] && ! running A; then
            a_done=1
            if wait "$relay_A"; then status=0; else status=$?; fi
            fact a_exit "$status"
            fact a_self 1
            fact a_exited_at "$now"
        fi
        if [ -z "$stranded_at" ] && "$PY" "$here/retire_judge.py" strand "$work" > "$work/strand.txt"; then
            stranded_at=$now
            fact stranded_at "$now"
            case $mode in
                retire|retire-red) break ;;
                pair-relay|pair-server)  # As today: A holds them until it is stopped and evicted.
                    fact a_self 0
                    fact a_stopped_at "$(date +%s)"
                    stop_pid "$relay_A"
                    a_done=1 ;;
            esac
        fi
        if [ "$(remaining)" = 0 ]; then break; fi
        waited=$((waited + 1))
        if [ "$waited" -ge "$deadline" ]; then echo "retire_drive: tasks remain after ${deadline}s" >&2; break; fi
        sleep 1
    done
    # A retiring relay exits once its clients have finished and their results have gone back.
    if [ "$mode" = retire ] && [ -z "$a_done" ] && [ -z "$stranded_at" ]; then
        n=0
        while running A && [ "$n" -lt 30 ]; do sleep 1; n=$((n + 1)); done
        if ! running A; then
            a_done=1
            if wait "$relay_A"; then status=0; else status=$?; fi
            fact a_exit "$status"
            fact a_self 1
        fi
    fi
    if [ -z "$a_done" ]; then
        fact a_self 0
        fact a_stopped_at "$(date +%s)"
        stop_pid "$relay_A"
    fi
    fact a2_running "$(running A2 && echo 1 || echo 0)"
    fact drained_at "$(date +%s)"
    if [ -n "${relay_A2:-}" ]; then
        if [ "$mode" = retire-red ]; then
            stop_pid "$relay_A2"
        else
            kill -USR1 "$relay_A2" 2>/dev/null || true
            n=0
            while kill -0 "$relay_A2" 2>/dev/null && [ "$n" -lt 60 ]; do sleep 1; n=$((n + 1)); done
            if kill -0 "$relay_A2" 2>/dev/null; then fact a2_drained 1; stop_pid "$relay_A2"
            else wait "$relay_A2" && fact a2_drained 0 || fact a2_drained 1; fi
        fi
    fi
fi

stop_pid "$sampler"
stop_pid "$server"
fact completed "$("$HS" list --completed --count 2>/dev/null || echo 0)"
fact failed "$("$HS" list --failed --count 2>/dev/null || echo 0)"

# --- Judgment and report --------------------------------------------------------------------------
if "$PY" "$here/retire_judge.py" judge "$work" "$mode" > "$work/judgment.txt"; then status=0; else status=$?; fi
relevant_lines() {
    echo "# server: registrations, duplicates, retirements, hand-backs, departures, evictions"
    grep -E 'Relay site (registered|left)|advertised by two relays|retired at site|Requeued|left the topology|Evicting client|Reverting orphaned|Refused hand-back' \
        server.log || true
    for dir in "$work"/relay-*/; do
        name=$(basename "$dir")
        echo "# $name: identity, topology, retirement, hand-back, staged revert, final beat, exit"
        grep -E 'Upstream identity|publishes|Topology .* \(zone:|Superseded|Handed back|hand back|Reverted .* staged|Upstream heartbeat - final|Holding .* tasks with no result|Terminating site client|Drain requested|Topology not read' \
            "$dir/relay.log" || true
    done
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
        echo "# retire_drive $mode: launches (epoch, launcher label, pid, {zone} word, HYPERSHELL_ZONE, client --zone)"
        cat "$log"
        echo "# retire_drive $mode: site clients running at A and A2 (epoch A A2), every second"
        cat "$work/alive.log"
        relevant_lines
        echo "# retire_drive $mode: facts"
        cat "$facts"
        echo "# retire_drive $mode: judgment"
        cat "$work/judgment.txt"
    } | sed -e "s|$root|<repo>|g" -e "s|$work|<site>|g" -e "s|$HOME|<home>|g" -e "s|$host|<host>|g" \
            -e "s|$short|<host>|g" -e "s|$user|<user>|g" -e "s|$key|<key>|g" \
        > "$DRIVE_EVIDENCE/$mode.txt"
fi
echo "retire_drive ($mode): $result"
exit "$status"
