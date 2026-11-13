#!/bin/sh
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0
#
# Laptop-scale drive of relay site-topology autoscaling: one `hs server --autoscaling=dynamic` with
# no launcher and two relays, A (zones cpu and highmem) and B (zones cpu and gpu), each a separate
# process on loopback with its own relay database, site and port, launching its site clients
# through topology_shim.sh, which records each launch. Run it in a throwaway site, from the
# repository root:
#
#   .agents/factory/bin/temp_site.sh sh "$PWD/spec/relay-topology/drive/topology_drive.sh" MODE
#
# The server evicts after 10 s and checks every second (except as noted); the relays beat every
# second, check their fleets every second, and their site clients exit after 2 idle seconds.
#
# Modes (tasks sleep one second unless noted):
#   spread  cpu, highmem and gpu work in group 0; cpu demand (40 tasks) exceeds one site's max (2)
#   fill    autoscale.sites = B; cpu demand (40 tasks) over B's max (2)
#   floor   cpu work in group 0; gpu and highmem work in group 1; B's gpu min is 1, A's highmem min 0
#   idle    a little work in every zone, then nothing: the clients leave and the relays stay
#   kill    autoscale.sites = B, factor 10, B's cpu max 20, A's 3; 40 cpu tasks of 2 seconds;
#           relay B is killed (SIGKILL, then its site clients) once 10 tasks are complete
#   lower   A runs with cpu max 3 and 60 cpu tasks; a second relay A2 for the same site A, with
#           cpu max 1, starts once A has 3 clients (its launches are recorded as site A2)
#   stale   the server checks every 20 s, 20 times the relay's period; relay A alone, cpu init 2 and
#           max 2, and no work: the server's silence between checks must not read as clients that
#           left before it counted them
#   bounce  relay A alone, cpu max 2, 40 cpu tasks of 2 seconds, eviction after 30 s; once A runs 2
#           clients it is killed (SIGKILL, then its site clients) and restarted at once as A2 (site A,
#           the same bounds, its own relay database; launches recorded as site A2)
#
# Checks, all modes: every task completes once and none fails; every launch names one zone alike
# in {zone}, HYPERSHELL_ZONE and --zone; no site's zone ever runs more clients than its max; no
# relay terminates a site client while it runs. Spread: cpu is launched at both A and B, highmem
# only at A, gpu only at B. Fill: B launches its max of cpu clients before A launches its first.
# Floor: a gpu client is launched before group 0's last completion; no highmem client is. Idle:
# after the work, every client exits by itself, nothing is launched again, and both relays stay
# connected (no relay identity leaves or is evicted) until drained. Kill: A launches no cpu client
# before B's eviction and launches cpu clients after it; B's orphaned tasks are reverted. Lower:
# the server warns naming both relays of site A; A launches nothing after that; each of A's
# clients ends by idle exit; A2 never runs more than one client, and launches within a few periods
# of the warning (A's clients no longer count against the site). Stale: once A's clients have left
# and the server has checked again, A has launched exactly its init (2), and never applied a version
# again. Bounce: the server warns naming both relays of site A; A2 launches within 10 s of its start
# and before A's eviction; A's orphaned tasks are reverted at its eviction.
#
# Settings: TD_DEADLINE, seconds each wait is bounded by (default 180). TD_EVIDENCE, a directory:
# when set, the launch log, the server's and relays' topology lines and the checks are written to
# MODE.txt there, with host, user and paths replaced. The script deletes nothing; it prints its
# scratch path and ends every process it started, on failure too.
set -eu

mode=${1:-spread}
here=$(cd "$(dirname "$0")" && pwd)
deadline=${TD_DEADLINE:-180}
work=$PWD
log=$work/launches.log
checks=$work/checks.txt
: > "$log"
: > "$checks"
echo "topology_drive ($mode): scratch $work"

HS=$(uv run --quiet python -c 'import os, sys; print(os.path.join(os.path.dirname(sys.executable), "hs"))')
PY=$(dirname "$HS")/python
PATH=$(dirname "$HS"):$PATH  # Site clients are launched as `hyper-shell client`.
export PATH
free_port() { "$PY" -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])'; }
port=$(free_port)
key="topology-drive-$("$PY" -c 'import secrets; print(secrets.token_hex(16))')"

# Every process this script starts, by pid: the server and the relays. Site clients are the
# relays'; any still running at the end are ended too (their pids are in the launch log).
started=
ended=
cleanup() {
    [ -n "$ended" ] && return
    ended=1
    for pid in $started; do kill -INT "$pid" 2>/dev/null || true; done
    waited=0
    for pid in $started; do
        while kill -0 "$pid" 2>/dev/null && [ "$waited" -lt 20 ]; do sleep 1; waited=$((waited + 1)); done
        kill -KILL "$pid" 2>/dev/null || true
    done
    for pid in $(sed -n 's/.* pid=\([0-9]*\) .*/\1/p' "$log"); do kill -KILL "$pid" 2>/dev/null || true; done
}
trap cleanup EXIT
trap 'exit 130' INT TERM

status=0
check() {  # NAME CONDITION-RESULT(0 pass)
    if [ "$2" = 0 ]; then echo "PASS $1" >> "$checks"; else echo "FAIL $1" >> "$checks"; status=1; fi
}
holds() { if "$@"; then echo 0; else echo 1; fi; }

# Tasks: NAME GROUP ZONE COUNT SECONDS
submit() {
    n=1
    while [ "$n" -le "$4" ]; do echo "sleep $5"; n=$((n + 1)); done > "tasks-$1.in"
    "$HS" submit -g "$2" --zone "$3" -f "tasks-$1.in" 2>/dev/null
}

# Relays: NAME SITE LABEL ZONES BOUNDS (TOML for the zone tables, with \n line breaks)
start_relay() {
    dir=$work/relay-$1
    mkdir -p "$dir"
    {
        printf '[relay]\nsite = "%s"\ntimeout = 2\nlauncher = "sh %s %s %s {zone}"\n' \
            "$2" "$here/topology_shim.sh" "$log" "$3"
        printf "$5"
    } > "$dir/relay.toml"
    zone_args=
    for zone in $4; do zone_args="$zone_args --zone $zone"; done
    env -u HYPERSHELL_DATABASE_FILE -u HYPERSHELL_AUTOSCALE_DISTRIBUTION -u HYPERSHELL_AUTOSCALE_SITES \
        HYPERSHELL_SITE="$dir" HYPERSHELL_CONFIG_FILE="$dir/relay.toml" \
        HYPERSHELL_RELAY_DATABASE="$dir/relay.db" \
        HYPERSHELL_RELAY_UPSTREAM_HOST=127.0.0.1 HYPERSHELL_RELAY_UPSTREAM_PORT="$port" \
        HYPERSHELL_RELAY_UPSTREAM_AUTH="$key" HYPERSHELL_RELAY_UPSTREAM_TLS_ENABLED=false \
        HYPERSHELL_RELAY_UPSTREAM_HEARTRATE=1 HYPERSHELL_SERVER_TLS_ENABLED=false \
        HYPERSHELL_AUTOSCALE_PERIOD=1 HYPERSHELL_LOGGING_LEVEL=DEBUG \
        "$HS" relay $zone_args --bind 127.0.0.1 --port "$(free_port)" 2> "$dir/relay.log" &
    eval "relay_$1=$!"
    started="$started $!"
}

# Workload and settings per mode.
distribution=spread sites= factor=1 period=1 evict=10 dead=
a_bounds='[relay.zone.cpu]\nmax = 2\n[relay.zone.highmem]\nmax = 2\n'
b_bounds='[relay.zone.cpu]\nmax = 2\n[relay.zone.gpu]\nmax = 2\n'
case $mode in
    spread) submit cpu 0 cpu 40 1; submit highmem 0 highmem 6 1; submit gpu 0 gpu 6 1 ;;
    fill) sites=B distribution=fill; submit cpu 0 cpu 40 1 ;;
    floor)
        b_bounds='[relay.zone.cpu]\nmax = 2\n[relay.zone.gpu]\nmin = 1\nmax = 2\n'
        submit cpu 0 cpu 20 1; submit gpu 1 gpu 4 1; submit highmem 1 highmem 4 1 ;;
    idle) submit cpu 0 cpu 6 1; submit highmem 0 highmem 2 1; submit gpu 0 gpu 2 1 ;;
    kill)
        sites=B distribution=fill factor=10 dead=B
        a_bounds='[relay.zone.cpu]\nmax = 3\n[relay.zone.highmem]\nmax = 1\n'
        b_bounds='[relay.zone.cpu]\nmax = 20\n[relay.zone.gpu]\nmax = 1\n'
        submit cpu 0 cpu 40 2 ;;
    lower)
        a_bounds='[relay.zone.cpu]\nmax = 3\n'
        submit cpu 0 cpu 60 1 ;;
    stale) period=20 a_bounds='[relay.zone.cpu]\ninit = 2\nmax = 2\n' ;;
    bounce)
        evict=30 dead=A
        a_bounds='[relay.zone.cpu]\nmax = 2\n'
        submit cpu 0 cpu 40 2 ;;
    *) echo "usage: topology_drive.sh spread|fill|floor|idle|kill|lower|stale|bounce" >&2; exit 2 ;;
esac
total=$("$HS" list --count)

env HYPERSHELL_SERVER_AUTH="$key" HYPERSHELL_SERVER_EVICT="$evict" HYPERSHELL_LOGGING_LEVEL=DEBUG HYPERSHELL_LOGGING_STYLE=system \
    HYPERSHELL_AUTOSCALE_DISTRIBUTION="$distribution" HYPERSHELL_AUTOSCALE_SITES="$sites" \
    "$HS" server --autoscaling=dynamic -P "$period" -F "$factor" --no-tls -H 127.0.0.1 -p "$port" 2> server.log &
server=$!
started="$started $server"

# The epoch of the server's first log line matching a pattern (its log is in the system style).
logged_at() {
    grep -m 1 -E "$1" server.log | "$PY" -c '
import sys
from datetime import datetime
line = sys.stdin.readline().split()
print(int(datetime.strptime(" ".join(line[:2]), "%Y-%m-%d %H:%M:%S.%f").timestamp()) if line else "")'
}
wait_registered() {  # SITE
    waited=0
    until grep -q "Relay site registered (site: $1," server.log; do
        waited=$((waited + 1))
        if [ "$waited" -ge "$deadline" ]; then echo "topology_drive: site $1 never registered" >&2; exit 1; fi
        sleep 1
    done
}

# B registers first, so the preference order of fill (B, then A) holds from the first plan.
if [ "$mode" = lower ] || [ "$mode" = stale ] || [ "$mode" = bounce ]; then
    start_relay A A A cpu "$a_bounds"
else
    start_relay B B B 'cpu gpu' "$b_bounds"
    wait_registered B
    start_relay A A A 'cpu highmem' "$a_bounds"
fi

# Peak running clients per launcher label and zone, sampled every second from the launch log.
peaks=$work/peaks.txt
: > "$peaks"
sample() {
    while read -r _ _ label pid word _; do
        kill -0 "${pid#pid=}" 2>/dev/null && echo "${label#site=} ${word#word=}"
    done < "$log" | sort | uniq -c >> "$peaks" || true
}
alive_count() {  # LABEL ZONE
    n=0
    while read -r _ _ label pid word _; do
        if [ "${label#site=}" = "$1" ] && [ "${word#word=}" = "$2" ] && kill -0 "${pid#pid=}" 2>/dev/null; then
            n=$((n + 1))
        fi
    done < "$log"
    echo "$n"
}
remaining() { "$HS" list --remaining --count 2>/dev/null || echo '?'; }
completed() { "$HS" list --completed --count 2>/dev/null || echo 0; }

killed_at= warned_at= evicted_at= restarted_at=
if [ "$mode" = stale ]; then
    # Nothing to run: wait for A's launch, for its clients to leave, then for the server's next check.
    waited=0
    until [ -s "$log" ] && [ "$(alive_count A cpu)" = 0 ]; do
        sample
        waited=$((waited + 1))
        if [ "$waited" -ge "$deadline" ]; then echo "FAIL A never launched, or its clients never left" >> "$checks"; status=1; break; fi
        sleep 1
    done
    left_at=$(date +%s)
    seen=$(grep -c 'Autoscale check (zone: cpu' server.log || true)
    echo "A's clients left at $left_at, after $seen server checks" >> "$checks"
    waited=0
    while [ "$(grep -c 'Autoscale check (zone: cpu' server.log || true)" -le "$seen" ]; do
        sample
        waited=$((waited + 1))
        if [ "$waited" -ge "$deadline" ]; then echo "FAIL the server never checked again" >> "$checks"; status=1; break; fi
        sleep 1
    done
    sleep 3  # The relay reads and applies what that check published.
    echo "the server checked again at $(date +%s)" >> "$checks"
fi
waited=0
while [ "$(remaining)" != 0 ]; do
    sample
    case $mode in
        kill)
            if [ -z "$killed_at" ] && [ "$(completed)" -ge 10 ]; then
                kill -KILL "$relay_B" 2>/dev/null || true
                killed_at=$(date +%s)
                for pid in $(sed -n 's/.* site=B pid=\([0-9]*\) .*/\1/p' "$log"); do kill -KILL "$pid" 2>/dev/null || true; done
                echo "killed relay B at $killed_at" >> "$checks"
            fi
            if [ -n "$killed_at" ] && [ -z "$evicted_at" ] && grep -q 'Reverting orphaned tasks' server.log; then
                evicted_at=$(logged_at 'Evicting client')
                echo "B evicted at $evicted_at (orphaned tasks reverted)" >> "$checks"
            fi
            ;;
        bounce)
            if [ -z "$killed_at" ] && [ "$(alive_count A cpu)" -ge 2 ]; then
                kill -KILL "$relay_A" 2>/dev/null || true
                killed_at=$(date +%s)
                for pid in $(sed -n 's/.* site=A pid=\([0-9]*\) .*/\1/p' "$log"); do kill -KILL "$pid" 2>/dev/null || true; done
                echo "killed relay A at $killed_at" >> "$checks"
                start_relay A2 A A2 cpu "$a_bounds"
                restarted_at=$(date +%s)
                echo "restarted as A2 at $restarted_at" >> "$checks"
            fi
            if [ -n "$killed_at" ] && [ -z "$warned_at" ] && grep -q 'advertised by two relays' server.log; then
                warned_at=$(logged_at 'advertised by two relays')
                echo "duplicate warning logged at $warned_at" >> "$checks"
            fi
            if [ -n "$killed_at" ] && [ -z "$evicted_at" ] && grep -q 'Reverting orphaned tasks' server.log; then
                evicted_at=$(logged_at 'Evicting client')
                echo "A evicted at $evicted_at (orphaned tasks reverted)" >> "$checks"
            fi
            ;;
        lower)
            if [ -z "${relay_A2:-}" ] && [ "$(alive_count A cpu)" -ge 3 ]; then
                start_relay A2 A A2 cpu '[relay.zone.cpu]\nmax = 1\n'
            fi
            if [ -n "${relay_A2:-}" ] && [ -z "$warned_at" ] && grep -q 'advertised by two relays' server.log; then
                warned_at=$(logged_at 'advertised by two relays')
                echo "duplicate warning logged at $warned_at" >> "$checks"
            fi
            ;;
    esac
    waited=$((waited + 1))
    if [ "$waited" -ge "$deadline" ]; then
        echo "FAIL tasks remain after ${deadline}s" >> "$checks"
        status=1
        break
    fi
    sleep 1
done
done_at=$(date +%s)
# The last plans may still be applied: a relay returns results up to bundlewait (5 s) after its
# clients ran them, and the server counts them as work until then. Let those versions land first.
sleep 6

# Every client leaves by its own idle timeout while the relays run, and nothing more is launched,
# except in a zone with a floor (floor mode: B's gpu, min 1), which the server holds with no work.
floored=' site=B .* word=gpu '
unfloored() { if [ "$mode" = floor ]; then grep -v -e "$floored" "$log" || true; else cat "$log"; fi; }
waited=0
while :; do
    sample
    alive=
    for pid in $(unfloored | sed -n 's/.* pid=\([0-9]*\) .*/\1/p'); do
        if kill -0 "$pid" 2>/dev/null; then alive=$pid; fi
    done
    [ -z "$alive" ] && break
    waited=$((waited + 1))
    if [ "$waited" -ge "$deadline" ]; then
        echo "FAIL client $alive still running after ${deadline}s" >> "$checks"
        status=1
        break
    fi
    sleep 1
done
launched_after_work=$(unfloored | wc -l)
floor_after_work=$(grep -c -e "$floored" "$log" || true)
sleep 5
check 'no launch after every client left' "$(holds test "$(unfloored | wc -l)" = "$launched_after_work")"
if [ "$mode" = floor ]; then
    # The held floor: B's gpu client idles out and is launched again, with no work left.
    waited=0
    while [ "$(grep -c -e "$floored" "$log" || true)" = "$floor_after_work" ] && [ "$waited" -lt 15 ]; do
        sleep 1; waited=$((waited + 1))
    done
    check "B's gpu floor (min 1) held after the work: its idle client launched again" \
        "$(holds test "$(grep -c -e "$floored" "$log" || true)" -gt "$floor_after_work")"
fi

relays_alive=0
for name in A B A2; do
    eval "pid=\${relay_$name:-}"
    [ -z "$pid" ] && continue
    [ "$name" = "$dead" ] && continue
    kill -0 "$pid" 2>/dev/null || relays_alive=1
done
check 'every live relay still runs after its clients left' "$relays_alive"
if [ -z "$dead" ]; then
    check 'no relay identity left or was evicted while running' \
        "$(holds sh -c '! grep -qE "Relay site left|Evicting client" server.log')"
fi

# Drain the relays (they finish cleanly), then stop the server.
for name in A B A2; do
    eval "pid=\${relay_$name:-}"
    [ -n "$pid" ] && kill -USR1 "$pid" 2>/dev/null || true
done
drained=0
for name in A B A2; do
    eval "pid=\${relay_$name:-}"
    [ -z "$pid" ] && continue
    [ "$name" = "$dead" ] && continue
    waited=0
    while kill -0 "$pid" 2>/dev/null && [ "$waited" -lt 60 ]; do sleep 1; waited=$((waited + 1)); done
    if kill -0 "$pid" 2>/dev/null; then drained=1; else wait "$pid" || drained=1; fi
done
check 'every live relay drained and exited 0' "$drained"
kill -INT "$server" 2>/dev/null || true
waited=0
while kill -0 "$server" 2>/dev/null && [ "$waited" -lt 30 ]; do sleep 1; waited=$((waited + 1)); done

# --- Checks on the record ------------------------------------------------------------------------
check "every task completed ($total)" "$(holds test "$(completed)" = "$total")"
check 'no task failed' "$(holds test "$("$HS" list --failed --count)" = 0)"
check 'some client was launched' "$(holds test -s "$log")"
check 'every launch names one zone in {zone}, HYPERSHELL_ZONE and --zone' \
    "$(holds awk '{ for (i = 5; i <= 7; i++) { split($i, kv, "="); v[i] = kv[2] } if (v[5] != v[6] || v[6] != v[7]) bad = 1 }
                  END { exit bad }' "$log")"
terminated=0
for dir in "$work"/relay-*/; do
    if grep -q 'Terminating site client' "$dir/relay.log"; then terminated=1; fi
done
check 'no relay terminated a site client' "$terminated"

# Peak running clients per site-zone never above the advertised max.
max_of() {  # LABEL ZONE
    case "$mode:$1:$2" in
        kill:A:cpu) echo 3 ;; kill:B:cpu) echo 20 ;; kill:*:*) echo 1 ;;
        lower:A:cpu) echo 3 ;; lower:A2:cpu) echo 1 ;;
        floor:B:gpu|*:*:*) echo 2 ;;
    esac
}
over=0
peak_lines=$(awk '{ key = $2 " " $3; if ($1 > peak[key]) peak[key] = $1 } END { for (k in peak) print k, peak[k] }' "$peaks" | sort)
echo "$peak_lines" | while read -r label zone peak; do
    [ -z "$label" ] && continue
    echo "peak $label $zone $peak (max $(max_of "$label" "$zone"))" >> "$checks"
done
for row in $(echo "$peak_lines" | tr ' ' ':'); do
    label=${row%%:*} rest=${row#*:}
    zone=${rest%%:*} peak=${rest#*:}
    [ "$peak" -le "$(max_of "$label" "$zone")" ] || over=1
done
check 'no site-zone ran more clients than its max' "$over"

launches_of() { grep -c " site=$1 .* word=$2 " "$log" || true; }
first_t() { sed -n "s/^launch t=\([0-9]*\) site=$1 .* word=$2 .*/\1/p" "$log" | head -n 1; }
g0_last() {
    "$HS" list -g 0 completion_time --json 2>/dev/null | "$PY" -c '
import sys, json
from datetime import datetime
rows = json.load(sys.stdin)
times = [datetime.fromisoformat(row["completion_time"]).timestamp() for row in rows if row.get("completion_time")]
print(int(max(times)) if times else 0)'
}

case $mode in
    spread)
        check 'cpu launched at A and at B' "$(holds test "$(launches_of A cpu)" -gt 0 -a "$(launches_of B cpu)" -gt 0)"
        check 'highmem launched only at A' "$(holds test "$(launches_of A highmem)" -gt 0 -a "$(launches_of B highmem)" = 0)"
        check 'gpu launched only at B' "$(holds test "$(launches_of B gpu)" -gt 0 -a "$(launches_of A gpu)" = 0)"
        ;;
    fill)
        before=$(awk '/ site=A .* word=cpu / { exit } / site=B .* word=cpu / { n++ } END { print n + 0 }' "$log")
        echo "B cpu launches before A's first: $before" >> "$checks"
        check "B reached its cpu max (2) before A's first cpu launch" "$(holds test "$before" -ge 2)"
        check 'A launched cpu once B was full' "$(holds test "$(launches_of A cpu)" -gt 0)"
        ;;
    floor)
        last=$(g0_last)
        gpu_t=$(first_t B gpu)
        echo "group 0 last completion $last; first gpu launch ${gpu_t:-none}" >> "$checks"
        check 'a gpu client (min 1) was launched while group 0 ran' "$(holds test -n "$gpu_t" -a "${gpu_t:-0}" -lt "$last")"
        early=$(awk -v last="$last" '/ word=highmem / { split($2, kv, "="); if (kv[2] < last) n++ } END { print n + 0 }' "$log")
        check 'no highmem client (min 0) was launched before group 0 ended' "$(holds test "$early" = 0)"
        check 'highmem was launched once group 1 began' "$(holds test "$(launches_of A highmem)" -gt 0)"
        ;;
    idle)
        unlaunched=0
        for pair in A:cpu A:highmem B:gpu; do
            [ "$(launches_of "${pair%:*}" "${pair#*:}")" -gt 0 ] || unlaunched=1
        done
        check 'every zone with work was launched into' "$unlaunched"
        idle_exits=$(cat "$work"/relay-*/relay.log | grep -c 'Autoscale client disconnected' || true)
        launch_count=$(wc -l < "$log" | tr -d ' ')
        echo "idle exits seen by the relays: $idle_exits of $launch_count launches" >> "$checks"
        check 'every client exit was seen by its relay as an idle exit' "$(holds test "$idle_exits" = "$launch_count")"
        ;;
    kill)
        check 'relay B was killed mid-run' "$(holds test -n "$killed_at")"
        check 'B was evicted and its orphaned tasks reverted' "$(holds test -n "$evicted_at")"
        a_before=$(awk -v t="${evicted_at:-0}" '/ site=A .* word=cpu / { split($2, kv, "="); if (kv[2] < t) n++ } END { print n + 0 }' "$log")
        a_after=$(awk -v t="${evicted_at:-0}" '/ site=A .* word=cpu / { split($2, kv, "="); if (kv[2] >= t) n++ } END { print n + 0 }' "$log")
        echo "A cpu launches before B's eviction: $a_before, after: $a_after" >> "$checks"
        check "A's cpu launches rose after B's eviction" "$(holds test "$a_after" -gt 0 -a "$a_before" = 0)"
        check 'the server logged B leaving the topology' "$(holds grep -q 'Site B left the topology' server.log)"
        ;;
    lower)
        check 'A2 started' "$(holds test -n "${relay_A2:-}")"
        check 'the server warned naming both relays of site A' "$(holds test -n "$warned_at")"
        late=$(awk -v t="${warned_at:-0}" '/ site=A pid/ { split($2, kv, "="); if (kv[2] > t) n++ } END { print n + 0 }' "$log")
        check 'A launched nothing after the warning' "$(holds test "$late" = 0)"
        a_launched=$(launches_of A cpu)
        a_idle=$(grep -c 'Autoscale client disconnected' "$work/relay-A/relay.log" || true)
        echo "A launched $a_launched; idle exits seen by A: $a_idle" >> "$checks"
        check "every one of A's clients ended by idle exit" "$(holds test "$a_idle" = "$a_launched")"
        a2_t=$(first_t A2 cpu)
        echo "A2's first cpu launch at ${a2_t:-none}" >> "$checks"
        check "A2 launched within 10 s of the warning, A's clients not counted against the site" \
            "$(holds test -n "$a2_t" -a "${a2_t:-0}" -le "$((${warned_at:-0} + 10))")"
        ;;
    stale)
        a_launched=$(launches_of A cpu)
        again=$(grep -c 'applying again' "$work/relay-A/relay.log" || true)
        echo "A launched $a_launched (init 2); versions applied again: $again" >> "$checks"
        check 'A launched exactly its init (2) across the server'"'"'s silence' "$(holds test "$a_launched" = 2)"
        check 'A applied no version again' "$(holds test "$again" = 0)"
        ;;
    bounce)
        check 'relay A was killed and restarted as A2' "$(holds test -n "$killed_at" -a -n "$restarted_at")"
        check 'the server warned naming both relays of site A' "$(holds test -n "$warned_at")"
        check "A was evicted and its orphaned tasks reverted" "$(holds test -n "$evicted_at")"
        a2_t=$(first_t A2 cpu)
        echo "A2's first cpu launch at ${a2_t:-none}: $((${a2_t:-0} - ${restarted_at:-0})) s after its start" >> "$checks"
        check 'A2 launched within 10 s of its start' \
            "$(holds test -n "$a2_t" -a "${a2_t:-0}" -le "$((${restarted_at:-0} + 10))")"
        check "A2 launched before A's eviction" "$(holds test -n "$a2_t" -a "${a2_t:-0}" -lt "${evicted_at:-0}")"
        ;;
esac

# --- Report --------------------------------------------------------------------------------------
topology_lines() {
    echo "# server: registrations, versions, duplicates, departures, evictions"
    grep -E 'Relay site (registered|left)|Topology .* published|Topology .* \(site:|advertised by two relays|left the topology|Evicting client|Reverting orphaned' \
        server.log || true
    for dir in "$work"/relay-*/; do
        name=$(basename "$dir")
        echo "# $name: topology reads and applications, idle exits"
        grep -E 'publishes|Topology .* \(zone:|Autoscale client disconnected|Terminating site client' "$dir/relay.log" || true
    done
}
echo '--- launches'
cat "$log"
echo '--- checks'
cat "$checks"
result=$([ "$status" = 0 ] && echo PASS || echo FAIL)
if [ -n "${TD_EVIDENCE:-}" ]; then
    mkdir -p "$TD_EVIDENCE"
    root=$(cd "$here/../../.." && pwd)
    host=$(hostname)
    short=$(hostname -s)
    user=${USER:-$(id -un)}
    {
        echo "# topology_drive $mode: launches (epoch, launcher label, pid, {zone} word, HYPERSHELL_ZONE, client --zone)"
        cat "$log"
        topology_lines
        echo "# topology_drive $mode: checks"
        cat "$checks"
        echo "# topology_drive $mode: $result"
    } | sed -e "s|$root|<repo>|g" -e "s|$work|<site>|g" -e "s|$HOME|<home>|g" -e "s|$host|<host>|g" \
            -e "s|$short|<host>|g" -e "s|$user|<user>|g" -e "s|$key|<key>|g" \
        > "$TD_EVIDENCE/$mode.txt"
fi
echo "topology_drive ($mode): $result"
exit "$status"
