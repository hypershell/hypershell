#!/bin/sh
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0
#
# Multi-node drive of a superseded relay's retirement (scenario `retire`), run inside one scheduler
# allocation of at least 4 nodes (one task per node). Built in the contract of the relay-topology
# drive (spec/relay-topology/drive/topology_cluster.sh, which stays as that cycle's record): the same
# environment, the same job steps, the same kind of record.
#
# Node 0 runs `hs server --autoscaling=fixed` with no launcher. Node 1 runs relay A (site A, zone
# cpu, init and max 3) and later relay A2 (site A too, init and max 2), as a relay restarted while
# the old process still runs. Each relay's launcher is the scheduler's own srun, one overlapping job
# step per site client, through retire_launch.sh and the recorder retire_record.sh; site clients
# land round robin on node 1 and the nodes after it, never on the server's. Every role is one step:
#
#   $HSD_SRUN --overlap --nodes=1 --ntasks=1 --nodelist=NODE --export=ALL $HSD_SRUN_ARGS ...
#
# The scenario is RV5 of the relay-topology review, with the laptop drive's calibration
# (spec/relay-retire/drive/retire_drive.sh): 12 cpu tasks of 40 s, site-client idle timeout 5 s.
# A's clients take what they can hold, find nothing more and pass their idle timeout while their
# tasks still run, and A keeps pulling for them; what it pulls then sits at A with no client to take
# it (the fixed policy launches no client while one runs). Once A has launched its 3 clients and has
# held a task no client took for 2 s running (relay A's role watches its relay database, read-only),
# A2 starts; the server holds the site for A2 and marks A retired. A then retires: it hands back what
# no client started, its clients finish, and it exits 0 by itself. Relays beat every 2 s, so R7's
# bound is 6 s from A's first "Superseded" line; eviction is set longer than the run, so eviction
# cannot rescue a stranded task. The 40 s tasks leave room for A2's start on a shared filesystem and
# for the mark to reach A before A's clients finish their first tasks (else A could relaunch and its
# new clients take what it holds: the run would prove nothing, and the judge says INCONCLUSIVE).
#
# Judged by retire_judge.py from the record retire_collect.py writes: every task completes, once;
# A2 starts at site A; A logs once that it was superseded, launches nothing after, terminates
# nothing, sends its final heartbeat, exits 0 by itself and is never evicted; A2 stays connected
# until drained; no task strands at A; the tasks A held unstarted at the mark are seen available
# upstream within 3 heartbeats of the mark plus the clock-skew allowance (A's log and the server's
# samples are on different nodes); A held or handed back something (else INCONCLUSIVE).
#
# Environment (nothing else is read from the caller; every HYPERSHELL_* variable is unset):
#   HS             the `hs` executable under test; `python` and `hyper-shell` must sit beside it, and
#                  all of it be readable from every node (required)
#   HSD_WORK       shared work directory: per-run transport (key, logs, launch records)
#                                                                       (default: ./retire-drive)
#   HSD_RESULTS    results directory: one record, <run id>.json (default: HSD_WORK/results)
#   HSD_NODE_TMP   node-local scratch, the same path on every node     (default: TMPDIR or /tmp)
#   HSD_SRUN_ARGS  extra flags for every job step, the relays' client steps included (default: none)
#   HSD_LABEL      label of the build under test, recorded [A-Za-z0-9._-] (default: feature)
#   HSD_RUN_ID     run id prefix [A-Za-z0-9._-]; the run id is <prefix>-retire
#                                                                       (default: retire-<date>-<time>)
# Rehearsal and tuning (neutral defaults; a cluster run sets none of them):
#   HSD_SRUN (srun), HSD_NODES (node names; default from `scontrol show hostnames`), HSD_LOCAL (1:
#   bind and dial loopback), HSD_TIMEOUT (900 s for the run), HSD_EVICT (1200 s; must exceed
#   HSD_TIMEOUT), HSD_POLL (1 s), HSD_STALL (180 s with no task completed once A2 has started ends
#   the run early), HSD_QUIET (5 s), HSD_PERIOD (1 s), HSD_HEARTRATE (2 s), HSD_CLIENT_TIMEOUT (5 s),
#   HSD_RELAY_PERIOD (1 s), HSD_TASKS (12), HSD_SLEEP (40 s per task), HSD_A_MAX (3), HSD_A2_MAX
#   (2), HSD_HOLD (2 s), HSD_SAMPLE (0.25 s)
#
# Every process this script starts is stopped on exit, on error, on a signal and at the run's
# deadline: the roles and recorders stop their own processes on the run's abort flag or deadline, and
# the job steps left after that are sent SIGTERM, then SIGKILL, by recorded pid. Nothing is deleted.
# Exit status: 0 if the run passed the judge, 1 otherwise (a failed or inconclusive run), 64 on bad
# usage.
set -eu

drive=$(cd -P "$(dirname "$0")" && pwd)
usage() { echo "retire_cluster.sh: $*" >&2; exit 64; }

: "${HS:?HS must name the hs executable under test}"
[ -x "$HS" ] || usage "HS is not executable: $HS"
HSD_PY=$(dirname "$HS")/python
[ -x "$HSD_PY" ] || usage "no python beside HS: $HSD_PY"
PATH="$(dirname "$HS"):$PATH"

HSD_WORK=${HSD_WORK:-$PWD/retire-drive}
HSD_RESULTS=${HSD_RESULTS:-$HSD_WORK/results}
HSD_NODE_TMP=${HSD_NODE_TMP:-${TMPDIR:-/tmp}}
HSD_LABEL=${HSD_LABEL:-feature}
HSD_RUN_ID=${HSD_RUN_ID:-retire-$(date +%Y%m%d-%H%M%S)}
HSD_SRUN=${HSD_SRUN:-srun}
HSD_SRUN_ARGS=${HSD_SRUN_ARGS:-}
HSD_TIMEOUT=${HSD_TIMEOUT:-900}
HSD_EVICT=${HSD_EVICT:-1200}
HSD_POLL=${HSD_POLL:-1}
HSD_STALL=${HSD_STALL:-180}
HSD_QUIET=${HSD_QUIET:-5}
case "$HSD_RUN_ID" in *[!A-Za-z0-9._-]*|'') usage "HSD_RUN_ID must match [A-Za-z0-9._-]+" ;; esac
case "$HSD_LABEL" in *[!A-Za-z0-9._-]*|'') usage "HSD_LABEL must match [A-Za-z0-9._-]+" ;; esac
case "$HSD_TIMEOUT$HSD_EVICT$HSD_POLL$HSD_STALL$HSD_QUIET" in *[!0-9]*) usage "timeouts must be whole seconds" ;; esac
[ "$HSD_TIMEOUT" -ge 300 ] || usage "HSD_TIMEOUT must be at least 300 s"
[ "$HSD_EVICT" -gt "$HSD_TIMEOUT" ] || usage "HSD_EVICT must exceed HSD_TIMEOUT (eviction must not rescue the run)"
mkdir -p "$HSD_WORK" "$HSD_RESULTS" "$HSD_NODE_TMP"
HSD_WORK=$(cd -P "$HSD_WORK" && pwd); HSD_RESULTS=$(cd -P "$HSD_RESULTS" && pwd)

# Isolation: no HyperShell setting from the caller's environment; every process logs in UTC, so
# the times of every node's logs compare.
for name in $(env | sed -n 's/^\(HYPERSHELL_[A-Z0-9_]*\)=.*/\1/p'); do unset "$name"; done
unset HSD_RUN HSD_DEADLINE HSD_SITE HSD_CLIENT_NODES HSD_LAUNCH_STATE
TZ=UTC
export TZ PATH HS HSD_PY HSD_WORK HSD_RESULTS HSD_NODE_TMP HSD_LABEL HSD_SRUN HSD_SRUN_ARGS HSD_TIMEOUT \
    HSD_EVICT HSD_QUIET

# Nodes: 0 the server, 1 relays A and A2; site clients on node 1 and the rest.
if [ -z "${HSD_NODES:-}" ]; then
    : "${SLURM_JOB_NODELIST:?run inside an allocation (or set HSD_NODES)}"
    HSD_NODES=$(scontrol show hostnames "$SLURM_JOB_NODELIST" | tr '\n' ' ')
fi
set -- $HSD_NODES
[ $# -ge 4 ] || usage "need at least 4 nodes, have $#"
HSD_NODES="$*"
HSD_SERVER_NODE=$1 node_relay=$2
shift 1
HSD_CLIENT_NODES="$*"
export HSD_NODES HSD_SERVER_NODE HSD_CLIENT_NODES
echo "retire_cluster: $(echo $HSD_NODES | wc -w | tr -d ' ') nodes; scenario: retire" >&2

now() { date +%s; }
stamp() {  # epoch seconds with a fraction, for the run's events
    t=$(date +%s.%N 2>/dev/null)
    case $t in *N*|'') "$HSD_PY" -c 'import time; print(f"{time.time():.3f}")' ;; *) echo "$t" ;; esac
}
event() { echo "$1 $(stamp)" >> "$run/events"; }
log() { echo "retire_cluster: ${run_id:-}: $*" >&2; }

# Job steps of the run, by the pid of their srun.
pids=
run=
step() {  # NODE NAME COMMAND...
    node=$1 name=$2
    shift 2
    $HSD_SRUN --overlap --nodes=1 --ntasks=1 --nodelist="$node" --export=ALL ${HSD_SRUN_ARGS:-} \
        "$@" > "$run/$name.out" 2>&1 &
    pids="$pids $!"
}
alive() { for pid in $pids; do kill -0 "$pid" 2>/dev/null && return 0; done; return 1; }
end_steps() {  # SECONDS: let the steps end by themselves, then SIGTERM, then SIGKILL
    waited=0
    while alive && [ "$waited" -lt "$1" ]; do sleep 1; waited=$((waited + 1)); done
    if alive; then
        log "job steps still running after $1 s: SIGTERM"
        for pid in $pids; do kill -TERM "$pid" 2>/dev/null || true; done
        waited=0
        while alive && [ "$waited" -lt 10 ]; do sleep 1; waited=$((waited + 1)); done
        for pid in $pids; do kill -KILL "$pid" 2>/dev/null || true; done
    fi
    for pid in $pids; do wait "$pid" 2>/dev/null || true; done
    pids=
}
cleanup() {
    [ -n "${cleaned:-}" ] && return
    cleaned=1
    if [ -n "$pids" ]; then
        log "stopping the run"
        : > "$run/abort"  # roles stop the server and relays; recorders end the site clients
        end_steps 45
    fi
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

wait_until() {  # SECONDS COMMAND...: poll a condition; 1 if it never holds, or the run is aborted
    limit=$1
    shift
    waited=0
    until "$@"; do
        [ -e "$run/abort" ] && return 1
        waited=$((waited + 1))
        [ "$waited" -lt "$limit" ] || return 1
        sleep 1
    done
}
registered() {  # COUNT: site A registered that many times (A, then A2)
    count=$(grep -c 'Relay site registered (site: A,' "$run/server.log" 2>/dev/null || true)
    [ "${count:-0}" -ge "$1" ]
}
exited() { [ -e "$run/relay-$1.exit" ]; }
progress() {  # sets total remaining launches open a_launches a2_launches
    total=-1 remaining=-1 launches=0 open=0 a_launches=0 a2_launches=0
    eval "$("$HSD_PY" "$drive/retire_role.py" status || true)"
}
all_left() { progress; [ "$launches" -gt 0 ] && [ "$open" = 0 ]; }

run_id="$HSD_RUN_ID-retire"
run="$HSD_WORK/$run_id"
[ ! -e "$run" ] || { log "$run exists: choose another HSD_RUN_ID"; exit 1; }
mkdir -p "$run/launches" "$run/runs"
( umask 077; od -An -N32 -tx1 /dev/urandom | tr -d ' \n' > "$run/key" )
printf '# The one configuration file every process of this run reads.\n[logging]\nlevel = "INFO"\n' > "$run/config.toml"
echo "start $(stamp)" > "$run/events"
start=$(now)
HSD_RUN=$run HSD_DEADLINE=$((start + HSD_TIMEOUT - 60))
export HSD_RUN HSD_DEADLINE
"$HSD_PY" "$drive/retire_role.py" plan
loop_end=$((start + HSD_TIMEOUT - 240))
status=0

# Server, then relay A; the work goes in once A is registered.
step "$HSD_SERVER_NODE" server "$HSD_PY" "$drive/retire_role.py" server
if ! wait_until 180 test -e "$run/server.ready"; then log "server did not start"; status=1; fi
if [ "$status" = 0 ]; then
    step "$node_relay" relay-A "$HSD_PY" "$drive/retire_role.py" relay A
    if ! wait_until 180 registered 1; then log "relay A never registered"; status=1; fi
fi
event registered
if [ "$status" = 0 ]; then
    : > "$run/submit"
    if ! wait_until 180 test -e "$run/submitted"; then log "the workload was not submitted"; status=1; fi
    event submitted
fi

# The work: until every task is complete; A2 starts on relay A's cue.
a2= a_seen= last_remaining= last_change=$(now)
while [ "$status" = 0 ]; do
    progress
    if [ "$total" -gt 0 ] && [ "$remaining" = 0 ]; then event completed; break; fi
    if [ -z "$a2" ] && [ -e "$run/held-A" ]; then
        event held
        step "$node_relay" relay-A2 "$HSD_PY" "$drive/retire_role.py" relay A2
        event a2
        a2=1
        log "relay A held a task no site client took: started relay A2 (A launches $a_launches)"
    fi
    if [ -n "$a2" ] && [ -z "$a_seen" ] && registered 2; then event a2-registered; a_seen=1; fi
    if [ -z "${a_exit:-}" ] && exited A; then event a-exited; a_exit=1; log "relay A exited"; fi
    if [ "$remaining" != "$last_remaining" ]; then last_remaining=$remaining last_change=$(now); fi
    if [ -n "$a2" ] && [ $(( $(now) - last_change )) -ge "$HSD_STALL" ]; then
        log "no task completed for $HSD_STALL s ($remaining of $total remain)"
        event stall
        status=1
        break
    fi
    if [ -e "$run/abort" ] || [ "$(now)" -ge "$loop_end" ]; then
        log "tasks remain at the deadline ($remaining of $total)"
        event deadline
        status=1
        break
    fi
    sleep "$HSD_POLL"
done

# A exits by itself once its clients have finished; every client then leaves by idle exit, A2 stays.
if [ "$status" = 0 ]; then
    if [ -z "${a_exit:-}" ]; then
        if wait_until 120 exited A; then event a-exited; else log "relay A still runs 120 s after the work"; fi
    fi
    if wait_until 180 all_left; then
        event quiet
        sleep "$HSD_QUIET"
        event observed
    else
        log "site clients still running 180 s after the work"
        event open
        status=1
    fi
fi

# Drain A2, stop A if it is still running, then stop the server; the roles record how each ended. A
# failed run is aborted first, so that every role stops at once.
[ "$status" = 0 ] || : > "$run/abort"
event drain
if [ -n "$a2" ]; then
    : > "$run/drain-A2"
    wait_until 120 exited A2 || log "relay A2 did not end after the drain"
fi
if ! exited A; then
    : > "$run/stop-A"
    wait_until 60 exited A || log "relay A did not end when stopped"
fi
: > "$run/stop-server"
wait_until 120 test -e "$run/server.exit" || log "server did not end"
end_steps 60
event end

"$HSD_PY" "$drive/retire_collect.py" "$run" "$HSD_RESULTS/$run_id.json" || status=1
"$HSD_PY" "$drive/retire_judge.py" "$HSD_RESULTS/$run_id.json" --require retire || status=1
log "$([ "$status" = 0 ] && echo PASS || echo FAIL) in $(( $(now) - start )) s; record $HSD_RESULTS/$run_id.json"
exit "$status"
