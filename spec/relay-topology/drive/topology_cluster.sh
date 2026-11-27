#!/bin/sh
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0
#
# Multi-node drive of relay site-topology autoscaling, run inside one scheduler allocation of at
# least 4 nodes (one task per node). Node 0 runs `hs server --autoscaling=dynamic` with no launcher.
# Node 1 runs relay A (site A, zones cpu and highmem) and node 2 relay B (site B, zones cpu and gpu).
# Each relay's launcher is the scheduler's own srun, one overlapping job step per site client, through
# topology_launch.sh and the recorder topology_record.sh: A's clients land on node 1 and the nodes
# after node 2, B's on node 2 and the same. Every role is one job step:
#
#   $HSD_SRUN --overlap --nodes=1 --ntasks=1 --nodelist=NODE --export=ALL $HSD_SRUN_ARGS ...
#
# Scenarios, each its own run with its own server, relays, key and deadline, judged by
# topology_judge.py from the record topology_collect.py writes:
#
#   spread-kill  distribution spread. Group 0 is highmem and gpu work (only A serves highmem, only B
#                gpu); group 1 is cpu work (both serve it, max HSD_CPU_MAX each). Once group 0 is done
#                and each site has launched HSD_KILL_AT_CLIENTS cpu clients, relay B and its site
#                clients are killed (SIGKILL); B is evicted, its orphaned tasks revert, and A's cpu
#                fleet grows again after the eviction.
#   fill         distribution fill, sites = A,B (B registers first, so the listed order is what makes
#                A preferred). Group 0 is cpu work (max HSD_FILL_MAX each); A launches its max of cpu
#                clients before B launches any. Group 1 is a little highmem and gpu work.
#
# Common checks: every task completes once and none fails, in group order; every launch names one
# zone alike in {zone}, HYPERSHELL_ZONE and --zone, at a site that serves it, on a node other than
# the server's; no site-zone runs more clients than its max; the upstream key is in no client's
# environment; every client exits by itself when idle (or by the kill), no relay terminates one, and
# nothing is launched once all have left; the live relays stay registered and drain with status 0.
#
# Environment (nothing else is read from the caller; every HYPERSHELL_* variable is unset):
#   HS             the `hs` executable under test; `python` and `hyper-shell` must sit beside it, and
#                  all of it be readable from every node (required)
#   HSD_WORK       shared work directory: per-run transport (key, logs, launch records)
#                                                                     (default: ./topology-drive)
#   HSD_RESULTS    results directory: one record per run, <run id>.json (default: HSD_WORK/results)
#   HSD_NODE_TMP   node-local scratch, the same path on every node   (default: TMPDIR or /tmp)
#   HSD_SRUN_ARGS  extra flags for every job step, the relays' client steps included (default: none)
#   HSD_LABEL      label of the build under test, recorded [A-Za-z0-9._-] (default: feature)
#   HSD_RUN_ID     run id prefix [A-Za-z0-9._-]; run ids are <prefix>-<scenario>
#                                                                     (default: topology-<date>-<time>)
# Rehearsal and tuning (neutral defaults; a cluster run sets none of them):
#   HSD_SRUN (srun), HSD_NODES (node names; default from `scontrol show hostnames`), HSD_LOCAL (1:
#   bind and dial loopback), HSD_SCENARIOS ("spread-kill fill"), HSD_TIMEOUT (900 s per run),
#   HSD_BUDGET (2400 s for all runs), HSD_POLL (2 s), HSD_QUIET (20 s), HSD_KILL_AT_CLIENTS (2),
#   HSD_PERIOD (5 s), HSD_FACTOR (1), HSD_EVICT (20 s), HSD_HEARTRATE (2 s), HSD_CLIENT_TIMEOUT (10 s),
#   HSD_THREADS (2), HSD_BUNDLEWAIT (2 s), HSD_RELAY_PERIOD (2 s), HSD_SLEEP (2 s per task),
#   HSD_TASKS (600 cpu tasks, spread-kill), HSD_SIDE_TASKS (16 highmem and 16 gpu), HSD_CPU_MAX (6),
#   HSD_SIDE_MAX (2), HSD_FILL_TASKS (300), HSD_FILL_MAX (4)
#
# Every process this script starts is stopped on exit, on error, on a signal and at each run's
# deadline: the roles and recorders stop their own processes on the run's abort flag or deadline, and
# the job steps left after that are sent SIGTERM, then SIGKILL, by recorded pid. Nothing is deleted.
# Exit status: 0 if every scenario ran and passed the judge, 1 otherwise, 64 on bad usage.
set -eu

drive=$(cd -P "$(dirname "$0")" && pwd)
usage() { echo "topology_cluster.sh: $*" >&2; exit 64; }

: "${HS:?HS must name the hs executable under test}"
[ -x "$HS" ] || usage "HS is not executable: $HS"
HSD_PY=$(dirname "$HS")/python
[ -x "$HSD_PY" ] || usage "no python beside HS: $HSD_PY"
PATH="$(dirname "$HS"):$PATH"

HSD_WORK=${HSD_WORK:-$PWD/topology-drive}
HSD_RESULTS=${HSD_RESULTS:-$HSD_WORK/results}
HSD_NODE_TMP=${HSD_NODE_TMP:-${TMPDIR:-/tmp}}
HSD_LABEL=${HSD_LABEL:-feature}
HSD_RUN_ID=${HSD_RUN_ID:-topology-$(date +%Y%m%d-%H%M%S)}
HSD_SRUN=${HSD_SRUN:-srun}
HSD_SRUN_ARGS=${HSD_SRUN_ARGS:-}
HSD_SCENARIOS=${HSD_SCENARIOS:-spread-kill fill}
HSD_TIMEOUT=${HSD_TIMEOUT:-900}
HSD_BUDGET=${HSD_BUDGET:-2400}
HSD_POLL=${HSD_POLL:-2}
HSD_QUIET=${HSD_QUIET:-20}
HSD_KILL_AT_CLIENTS=${HSD_KILL_AT_CLIENTS:-2}
case "$HSD_RUN_ID" in *[!A-Za-z0-9._-]*|'') usage "HSD_RUN_ID must match [A-Za-z0-9._-]+" ;; esac
case "$HSD_LABEL" in *[!A-Za-z0-9._-]*|'') usage "HSD_LABEL must match [A-Za-z0-9._-]+" ;; esac
case "$HSD_TIMEOUT$HSD_BUDGET$HSD_QUIET$HSD_KILL_AT_CLIENTS$HSD_POLL" in *[!0-9]*) usage "timeouts and counts must be whole numbers" ;; esac
[ "$HSD_TIMEOUT" -ge 300 ] || usage "HSD_TIMEOUT must be at least 300 s"
for scenario in $HSD_SCENARIOS; do
    case "$scenario" in spread-kill|fill) ;; *) usage "unknown scenario: $scenario" ;; esac
done
mkdir -p "$HSD_WORK" "$HSD_RESULTS" "$HSD_NODE_TMP"
HSD_WORK=$(cd -P "$HSD_WORK" && pwd); HSD_RESULTS=$(cd -P "$HSD_RESULTS" && pwd)

# Isolation: no HyperShell setting from the caller's environment; every process logs in UTC, so
# the times of every node's logs compare.
for name in $(env | sed -n 's/^\(HYPERSHELL_[A-Z0-9_]*\)=.*/\1/p'); do unset "$name"; done
unset HSD_RUN HSD_DEADLINE HSD_SITE HSD_CLIENT_NODES HSD_LAUNCH_STATE
TZ=UTC
export TZ PATH HS HSD_PY HSD_WORK HSD_RESULTS HSD_NODE_TMP HSD_LABEL HSD_SRUN HSD_SRUN_ARGS HSD_KILL_AT_CLIENTS HSD_QUIET

# Nodes: 0 the server, 1 relay A, 2 relay B; client steps on each relay's own node and on the rest.
if [ -z "${HSD_NODES:-}" ]; then
    : "${SLURM_JOB_NODELIST:?run inside an allocation (or set HSD_NODES)}"
    HSD_NODES=$(scontrol show hostnames "$SLURM_JOB_NODELIST" | tr '\n' ' ')
fi
set -- $HSD_NODES
[ $# -ge 4 ] || usage "need at least 4 nodes, have $#"
HSD_NODES="$*"
HSD_SERVER_NODE=$1 node_a=$2 node_b=$3
shift 3
HSD_CLIENT_NODES_A="$node_a $*" HSD_CLIENT_NODES_B="$node_b $*"
export HSD_NODES HSD_SERVER_NODE HSD_CLIENT_NODES_A HSD_CLIENT_NODES_B
echo "topology_cluster: $(echo $HSD_NODES | wc -w | tr -d ' ') nodes; scenarios: $HSD_SCENARIOS" >&2

now() { date +%s; }
stamp() {  # epoch seconds with a fraction, for the run's events
    t=$(date +%s.%N 2>/dev/null)
    case $t in *N*|'') "$HSD_PY" -c 'import time; print(f"{time.time():.3f}")' ;; *) echo "$t" ;; esac
}
event() { echo "$1 $(stamp)" >> "$run/events"; }
log() { echo "topology_cluster: ${run_id:-}: $*" >&2; }

# Job steps of the current run, by the pid of their srun.
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
registered() { grep -q "Relay site registered (site: $1," "$run/server.log" 2>/dev/null; }
exited() { [ -e "$run/relay-$1.exit" ]; }
progress() {  # sets total remaining first_group_left launches open a_cpu b_cpu
    total=-1 remaining=-1 first_group_left=-1 launches=0 open=0 a_cpu=0 b_cpu=0
    eval "$("$HSD_PY" "$drive/topology_role.py" status || true)"
}
all_left() { progress; [ "$launches" -gt 0 ] && [ "$open" = 0 ]; }

run_one() {  # SCENARIO
    scenario=$1
    run_id="$HSD_RUN_ID-$scenario"
    run="$HSD_WORK/$run_id"
    [ ! -e "$run" ] || { log "$run exists: choose another HSD_RUN_ID"; return 1; }
    mkdir -p "$run/launches"
    ( umask 077; od -An -N32 -tx1 /dev/urandom | tr -d ' \n' > "$run/key" )
    printf '# The one configuration file every process of this run reads.\n[logging]\nlevel = "INFO"\n' > "$run/config.toml"
    echo "start $(stamp)" > "$run/events"
    start=$(now)
    HSD_RUN=$run HSD_DEADLINE=$((start + HSD_TIMEOUT - 60))
    export HSD_RUN HSD_DEADLINE
    "$HSD_PY" "$drive/topology_role.py" plan "$scenario"
    loop_end=$((start + HSD_TIMEOUT - 240))
    status=0

    # Server, then relay B, then relay A: B registers first.
    step "$HSD_SERVER_NODE" server "$HSD_PY" "$drive/topology_role.py" server
    if ! wait_until 180 test -e "$run/server.ready"; then log "server did not start"; status=1; fi
    if [ "$status" = 0 ]; then
        step "$node_b" relay-B "$HSD_PY" "$drive/topology_role.py" relay B
        if ! wait_until 180 registered B; then log "relay B never registered"; status=1; fi
    fi
    if [ "$status" = 0 ]; then
        step "$node_a" relay-A "$HSD_PY" "$drive/topology_role.py" relay A
        if ! wait_until 180 registered A; then log "relay A never registered"; status=1; fi
    fi
    event registered
    # The work goes in only now, so the first plan with work sees both sites.
    if [ "$status" = 0 ]; then
        : > "$run/submit"
        if ! wait_until 180 test -e "$run/submitted"; then log "the workload was not submitted"; status=1; fi
        event submitted
    fi

    # The work: until every task is complete; kill relay B once (spread-kill).
    killed=
    while [ "$status" = 0 ]; do
        progress
        if [ "$total" -gt 0 ] && [ "$remaining" = 0 ]; then event completed; break; fi
        if [ "$scenario" = spread-kill ] && [ -z "$killed" ] && [ "$first_group_left" = 0 ] \
                && [ "$a_cpu" -ge "$HSD_KILL_AT_CLIENTS" ] && [ "$b_cpu" -ge "$HSD_KILL_AT_CLIENTS" ]; then
            : > "$run/kill-B"  # the role SIGKILLs relay B; B's recorders SIGKILL its site clients
            event kill
            killed=1
            log "killed relay B (A cpu launches $a_cpu, B cpu launches $b_cpu)"
        fi
        if [ -e "$run/abort" ] || [ "$(now)" -ge "$loop_end" ]; then
            log "tasks remain at the deadline ($remaining of $total)"
            event deadline
            status=1
            break
        fi
        sleep "$HSD_POLL"
    done

    # Idle exit: every client leaves by itself; then nothing is launched while the relays stay.
    if [ "$status" = 0 ]; then
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

    # Drain the relays, then stop the server; the roles record how each ended. A failed run is
    # aborted first, so that every role stops at once.
    [ "$status" = 0 ] || : > "$run/abort"
    event drain
    : > "$run/drain-A"; : > "$run/drain-B"
    wait_until 120 exited A || log "relay A did not end after the drain"
    wait_until 120 exited B || log "relay B did not end after the drain"
    : > "$run/stop-server"
    wait_until 120 test -e "$run/server.exit" || log "server did not end"
    end_steps 60
    event end

    "$HSD_PY" "$drive/topology_collect.py" "$run" "$HSD_RESULTS/$run_id.json" || status=1
    "$HSD_PY" "$drive/topology_judge.py" "$HSD_RESULTS/$run_id.json" || status=1
    log "$([ "$status" = 0 ] && echo PASS || echo FAIL) in $(( $(now) - start )) s; record $HSD_RESULTS/$run_id.json"
    return "$status"
}

began=$(now)
overall=0
for scenario in $HSD_SCENARIOS; do
    if [ $(( $(now) - began + HSD_TIMEOUT )) -gt "$HSD_BUDGET" ]; then
        echo "topology_cluster: skipping $scenario: it could end past the $HSD_BUDGET s budget" >&2
        overall=1
        continue
    fi
    run_one "$scenario" || overall=1
done
echo "topology_cluster: $([ "$overall" = 0 ] && echo PASS || echo FAIL) in $(( $(now) - began )) s; records in $HSD_RESULTS" >&2
exit "$overall"
