#!/bin/sh
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0
#
# Laptop-scale drive of zone-aware autoscaling: one `hs cluster --autoscaling` on this machine,
# launching its clients through zone_shim.sh, which records each launch. No scheduler is used.
# Run it in a throwaway site, from the repository root:
#
#   .agents/factory/bin/temp_site.sh sh "$PWD/spec/zone-autoscale/drive/zone_drive.sh" MODE
#
# Modes:
#   founding  default zone only, fixed policy, no --zone: the run without zones
#   fixed     default and cpu tasks in group 0, gpu tasks in group 1, fixed policy
#   dynamic   the same three zones under the dynamic policy, with tasks that take a while
#
# Checks, all modes: every task completes and none fails; every launched client exits by itself
# (idle exit) while the cluster still runs. Founding: every launch is in the default zone with no
# --zone, and names the default zone in {zone} and HYPERSHELL_ZONE. Fixed and dynamic: each zone
# is launched into, every launch names one zone, no gpu launch is made while group 0 has work,
# and each task sees its own zone in HYPERSHELL_ZONE.
#
# Settings: ZD_DEADLINE, seconds each wait is bounded by (default 120).
set -eu

mode=${1:-founding}
here=$(cd "$(dirname "$0")" && pwd)
deadline=${ZD_DEADLINE:-120}
log=$PWD/launches.log out=$PWD/tasks.log
: > "$log"
: > "$out"

case $mode in
    founding) zones='default:0' policy=fixed count=3 work=0 ;;
    fixed) zones='default:0 cpu:0 gpu:1' policy=fixed count=3 work=0 ;;
    dynamic) zones='default:0 cpu:0 gpu:1' policy=dynamic count=12 work=1 ;;
    *) echo "usage: zone_drive.sh founding|fixed|dynamic" >&2; exit 2 ;;
esac

for pair in $zones; do
    zone=${pair%%:*} group=${pair#*:}
    n=1
    while [ "$n" -le "$count" ]; do
        printf '%s\n' "sh -c 'sleep $work; echo $zone \${HYPERSHELL_ZONE:--} $n >> $out'"
        n=$((n + 1))
    done > "tasks-$zone.in"
    if [ "$zone" = default ]; then
        uv run --quiet hs submit -g "$group" -f "tasks-$zone.in"
    else
        uv run --quiet hs submit -g "$group" --zone "$zone" -f "tasks-$zone.in"
    fi
done 2>/dev/null

port=$(uv run --quiet python -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
HYPERSHELL_LOGGING_LEVEL=DEBUG uv run --quiet hs cluster --restart --autoscaling="$policy" \
    --launcher="sh $here/zone_shim.sh $log {zone}" \
    -H 127.0.0.1 -p "$port" -T 2 -P 1 -I 1 -X 0 > cluster.out 2>&1 &
pid=$!

status=0
fail() {
    echo "zone_drive: $*" >&2
    status=1
}

waited=0
while [ "$(uv run --quiet hs list --remaining --count)" != 0 ]; do
    waited=$((waited + 1))
    if [ "$waited" -ge "$deadline" ]; then
        fail "tasks remain after ${deadline}s"
        break
    fi
    sleep 1
done

# Every client must leave on its own idle timeout while the cluster runs (no termination), and
# the autoscaler must not launch again once the work is done.
waited=0
while :; do
    alive=
    for client in $(sed -n 's/^launch pid=\([0-9]*\) .*/\1/p' "$log"); do
        if kill -0 "$client" 2>/dev/null; then alive=$client; fi
    done
    [ -z "$alive" ] && break
    waited=$((waited + 1))
    if [ "$waited" -ge "$deadline" ]; then
        fail "client $alive still running after ${deadline}s"
        break
    fi
    sleep 1
done
launched_before_stop=$(wc -l < "$log")
sleep 3

kill "$pid" 2>/dev/null || true
wait "$pid" 2>/dev/null || true

echo '--- launches'
cat "$log"
echo '--- tasks'
sort "$out"
echo '--- cluster (autoscaler)'
grep -E 'Autoscale (check|adding|start|launcher)' cluster.out | sed 's/^/  /' | tail -n 40 || true

[ -s "$log" ] || fail 'no client was launched'
[ "$(wc -l < "$log")" = "$launched_before_stop" ] || fail 'a client was launched after the work was done'
[ "$(uv run --quiet hs list --failed --count)" = 0 ] || fail 'some tasks failed'
[ "$(uv run --quiet hs list --remaining --count)" = 0 ] || fail 'some tasks never completed'

# Every task ran once, in the expected zones.
expected=$(for pair in $zones; do echo "${pair%%:*}"; done | sort | tr '\n' ' ')
awk -v count="$count" -v zones="$expected" '
    { n[$1]++ }
    END {
        split(zones, want, " ")
        for (i in want) if (n[want[i]] != count) exit 1
        for (z in n) { found = 0; for (i in want) if (want[i] == z) found = 1; if (!found) exit 1 }
    }' "$out" || fail "tasks per zone differ from $count each in: $expected"

case $mode in
    founding)
        awk '{ split($5, kv, "="); if (kv[2] != "-") exit 1 }' "$log" \
            || fail 'a launch named a zone (--zone) in the run without zones'
        awk '$3 != "word=default" || $4 != "env=default" { exit 1 }' "$log" \
            || fail 'a launch did not name the default zone in {zone} and HYPERSHELL_ZONE'
        ;;
    fixed|dynamic)
        # word == env == --zone, except the own default zone, which keeps today's command line.
        awk '{ for (i = 3; i <= 6; i++) { split($i, kv, "="); v[i] = kv[2] }
               if (v[3] != v[4]) exit 1
               if (v[5] != v[3] && !(v[3] == "default" && v[5] == "-")) exit 1 }' "$log" \
            || fail 'a launch disagrees on its zone across {zone}, HYPERSHELL_ZONE and --zone'
        for zone in default cpu gpu; do
            grep -q " word=$zone " "$log" || fail "no client was launched into zone $zone"
        done
        awk '$3 == "word=gpu" && $6 != "g0=0" { exit 1 }' "$log" \
            || fail 'a gpu client was launched while group 0 had work'
        awk '$1 != $2 { exit 1 }' "$out" || fail 'a task saw another zone in HYPERSHELL_ZONE'
        ;;
esac

if [ "$mode" = dynamic ]; then
    awk '{ n[$3]++ } END { grew = 0; one = 0
                          for (z in n) { if (n[z] > 1) grew = 1; if (n[z] == 1) one = 1 }
                          exit !(grew && one) }' "$log" \
        || fail 'no zone grew past one client on its own pressure while another stayed at one'
fi

echo "zone_drive ($mode): $([ "$status" = 0 ] && echo PASS || echo FAIL)"
exit "$status"
