#!/bin/sh
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0
#
# Multi-node relay drive: one upstream server and two sites, each with its own `hs relay` launching
# site clients on its own nodes with a site-local key, on a grouped workload with failing-and-retried
# tasks. Checks, judged by relay_judge.py from the records written here:
#   (a) every task reaches its final state once upstream, with group order kept;
#   (b) every completed row names a leaf client of the site that ran it, and the leaves appear
#       upstream attributed to their site;
#   (c) the upstream server holds connections for the relays only, never for leaf clients;
#   (d) failover: relay B is stopped mid-run (kill, or pause), evicted, its unreturned tasks revert
#       and complete at site A, and no task is lost (variant none is the control);
#   (e) the upstream key appears in no site client's (or task's) argv or environment. Also recorded:
#       each site client holds its site key in its environment (HYPERSHELL_SERVER_AUTH), and no
#       command line and no task holds it.
#
# Runs inside one Slurm allocation of at least 3 nodes (or on one machine with HSD_MODE=local, see
# relay_smoke.sh). Node 0 runs the upstream server; the remaining nodes split into site A (the first
# HSD_SITE_A_SIZE of them) and site B (the rest). Each site's first node runs its relay; every site
# node runs a watcher that scans the site's processes. Every role is one job step (--overlap); the
# relays launch site clients through relay_launch.sh, one job step each. Nothing here names a site,
# a cluster, an account or a path: every site-specific value comes from the variables below.
#
#   relay_drive.sh           run the matrix HSD_RUNS inside the allocation; judge each run
#   relay_drive.sh submit    submit this script as a batch job (sbatch), then exit
#   relay_drive.sh role ...  internal: one role on this node (see relay_probe.py)
#
# Required:
#   HS                    the `hs` executable of the HyperShell under test (e.g. a venv's bin/hs); its
#                         directory must hold `python` (with psutil) and `hyper-shell`, and be readable
#                         from every node
# Placement (shared filesystem, visible from every node):
#   HSD_WORK              transport root: key, flags, logs; never evidence (default: ./relay-drive)
#   HSD_RESULTS           evidence root; each run writes HSD_RESULTS/<run_id>/*.json (default: HSD_WORK/results)
#   HSD_NODE_TMP          node-local scratch on every node, same path everywhere (default: TMPDIR or /tmp)
# Site (all optional, neutral defaults):
#   HSD_SETUP             shell text evaluated first in every step (e.g. module loads)   (default: none)
#   HSD_SRUN              step launcher prefix                                           (default: srun)
#   HSD_SRUN_ARGS         extra flags for every job step (e.g. --gres=none)              (default: none)
#   HSD_LEAF_SRUN_ARGS    extra flags for site client steps only                         (default: none)
#   HSD_SBATCH            batch submitter, for `submit`                                  (default: sbatch)
#   HSD_ACCOUNT / HSD_PARTITION   for `submit`                                            (default: none)
#   HSD_SUBMIT_NODES      node count, for `submit`                                       (default: 3)
#   HSD_TIME_LIMIT        wall limit, for `submit`                                       (default: 00:20:00)
#   HSD_SBATCH_ARGS       extra sbatch flags, for `submit`                               (default: none)
# Run matrix:
#   HSD_RUN_ID            prefix of run ids, [A-Za-z0-9._-]+     (default: relay-<date>-<time>)
#   HSD_LABEL             label of the build under test, recorded               (default: feature)
#   HSD_RUNS              variants in order, each kill | pause | none    (default: "kill pause none")
#   HSD_TIMEOUT           per-run deadline for the workload, s                          (default: 300)
#   HSD_BUDGET            skip a run that could end past this many s from start         (default: 840)
#   HSD_SITE_A_SIZE       nodes in site A                  (default: half the site nodes, rounded up)
# Workload and timing:
#   HSD_TASKS (2000), HSD_FAILING (3), HSD_GROUPS (3), HSD_SLEEP (1.0 s per task), HSD_ZONE (shared)
#   HSD_THREADS (8 executors per site client), HSD_CLIENTS_PER_NODE (2), HSD_RELAY_BUNDLESIZE (4 x
#   threads: a site holds at most its clients x this many x 2 tasks), HSD_EVICT (30 s upstream),
#   HSD_HEARTRATE (5 s relay to upstream), HSD_KILL_AT (0.4 of rows completed), HSD_PAUSE (evict + 15 s),
#   HSD_SETTLE (4 s), HSD_SAMPLE (1 s), HSD_TLS (1), HSD_SERVER_BUNDLESIZE (8), HSD_SERVER_POLL (2 s)
#
# Exit status: 0 if every run that ran passed the judge, 1 otherwise, 64 on bad usage.
set -eu

drive=$(cd -P "$(dirname "$0")" && pwd)
usage() { echo "relay_drive.sh: $*" >&2; exit 64; }

eval "${HSD_SETUP:-}"
: "${HS:?HS must name the hs executable of the HyperShell under test}"
[ -x "$HS" ] || usage "HS is not executable: $HS"
HSD_PY=${HSD_PY:-$(dirname "$HS")/python}
[ -x "$HSD_PY" ] || usage "no python beside HS: $HSD_PY"
PATH="$(dirname "$HS"):$PATH"
export HS HSD_PY PATH

if [ "${1:-}" = role ]; then
    shift
    exec "$HSD_PY" "$drive/relay_probe.py" "$@"
fi

HSD_WORK=${HSD_WORK:-$PWD/relay-drive}
HSD_RESULTS=${HSD_RESULTS:-$HSD_WORK/results}
mkdir -p "$HSD_WORK" "$HSD_RESULTS"
HSD_WORK=$(cd -P "$HSD_WORK" && pwd); HSD_RESULTS=$(cd -P "$HSD_RESULTS" && pwd)
export HSD_WORK HSD_RESULTS

if [ "${1:-}" = submit ]; then
    exec ${HSD_SBATCH:-sbatch} --nodes="${HSD_SUBMIT_NODES:-3}" --time="${HSD_TIME_LIMIT:-00:20:00}" \
        ${HSD_ACCOUNT:+--account="$HSD_ACCOUNT"} ${HSD_PARTITION:+--partition="$HSD_PARTITION"} \
        ${HSD_SBATCH_ARGS:-} --export=ALL --output="$HSD_WORK/relay-drive-%j.out" "$drive/relay_drive.sh"
fi
[ $# -eq 0 ] || usage "unknown arguments: $*"

HSD_MODE=${HSD_MODE:-slurm}
HSD_RUN_ID=${HSD_RUN_ID:-relay-$(date +%Y%m%d-%H%M%S)}
HSD_LABEL=${HSD_LABEL:-feature}
HSD_RUNS=${HSD_RUNS:-kill pause none}
HSD_TIMEOUT=${HSD_TIMEOUT:-300}
HSD_BUDGET=${HSD_BUDGET:-840}
HSD_SRUN=${HSD_SRUN:-srun}
case "$HSD_MODE" in slurm|local) ;; *) usage "HSD_MODE must be slurm or local" ;; esac
case "$HSD_RUN_ID" in *[!A-Za-z0-9._-]*|'') usage "HSD_RUN_ID must match [A-Za-z0-9._-]+" ;; esac
case "$HSD_LABEL" in *[!A-Za-z0-9._-]*|'') usage "HSD_LABEL must match [A-Za-z0-9._-]+" ;; esac
for variant in $HSD_RUNS; do
    case "$variant" in kill|pause|none) ;; *) usage "HSD_RUNS: unknown variant $variant" ;; esac
done
case "$HSD_TIMEOUT$HSD_BUDGET" in *[!0-9]*) usage "HSD_TIMEOUT and HSD_BUDGET must be whole seconds" ;; esac
case "$HSD_WORK$HSD_RESULTS$drive" in *[\'{}]*) usage "paths must not hold quotes or braces" ;; esac

# Isolation: no HyperShell setting from the caller's environment, and only the configuration file
# written for each run (HYPERSHELL_CONFIG_FILE), never a user or system one.
for name in $(env | sed -n 's/^\(HYPERSHELL_[A-Z0-9_]*\)=.*/\1/p'); do unset "$name"; done
unset HSD_SITE HSD_RUN_TOKEN HSD_EXEC_LOG HSD_KEY_PROBE HSD_SITE_NODES HSD_LAUNCH_STATE
export HSD_MODE HSD_LABEL HSD_TIMEOUT HSD_SRUN

# Nodes: 0 is upstream; the rest split into site A and site B.
if [ "$HSD_MODE" = slurm ]; then
    : "${SLURM_JOB_NODELIST:?run inside a Slurm allocation (or set HSD_MODE=local)}"
    nodes=$(scontrol show hostnames "$SLURM_JOB_NODELIST" | tr '\n' ' ')
else
    nodes="node-0 node-1 node-2"
fi
set -- $nodes
[ $# -ge 3 ] || usage "need at least 3 nodes, have $#"
HSD_UPSTREAM_NODE=$1; shift
a_size=${HSD_SITE_A_SIZE:-$(( ($# + 1) / 2 ))}
[ "$a_size" -ge 1 ] && [ "$a_size" -lt $# ] || usage "HSD_SITE_A_SIZE must leave both sites a node"
HSD_SITE_A_NODES=; HSD_SITE_B_NODES=; i=0
for node; do
    i=$((i + 1))
    if [ "$i" -le "$a_size" ]; then HSD_SITE_A_NODES="$HSD_SITE_A_NODES $node"; else HSD_SITE_B_NODES="$HSD_SITE_B_NODES $node"; fi
done
HSD_SITE_A_NODES=${HSD_SITE_A_NODES# }; HSD_SITE_B_NODES=${HSD_SITE_B_NODES# }
export HSD_UPSTREAM_NODE HSD_SITE_A_NODES HSD_SITE_B_NODES
echo "relay_drive: mode $HSD_MODE; upstream 1 node, site A $(echo $HSD_SITE_A_NODES | wc -w | tr -d ' ') nodes, site B $(echo $HSD_SITE_B_NODES | wc -w | tr -d ' ') nodes" >&2

pids=
start_role() {  # node name role-args...
    node=$1; name=$2; shift 2
    if [ "$HSD_MODE" = slurm ]; then
        $HSD_SRUN --overlap --nodes=1 --ntasks=1 --nodelist="$node" --export=ALL ${HSD_SRUN_ARGS:-} \
            sh "$drive/relay_drive.sh" role "$@" > "$HSD_RUN/$name.out" 2>&1 &
    else
        HSD_NODE_TMP="$HSD_WORK/nodes/$node" sh "$drive/relay_drive.sh" role "$@" > "$HSD_RUN/$name.out" 2>&1 &
    fi
    pids="$pids $!"
}

wait_roles() {  # seconds: wait for every role step; past the bound, abort, then end them
    waited=0
    while :; do
        alive=0
        for pid in $pids; do kill -0 "$pid" 2>/dev/null && alive=1; done
        [ "$alive" = 1 ] || break
        if [ "$waited" -ge "$1" ]; then
            echo "relay_drive: roles still running after $1 s - aborting the run" >&2
            echo deadline > "$HSD_RUN/abort"
            sleep 30
            for pid in $pids; do kill -TERM "$pid" 2>/dev/null || true; done
            sleep 5
            for pid in $pids; do kill -KILL "$pid" 2>/dev/null || true; done
            break
        fi
        sleep 1; waited=$((waited + 1))
    done
    rc=0
    for pid in $pids; do wait "$pid" || rc=1; done
    pids=
    return "$rc"
}

run_one() {  # index variant
    run_id="$HSD_RUN_ID-r$1-$2"
    HSD_RUN="$HSD_WORK/$run_id"; HSD_OUT="$HSD_RESULTS/$run_id"
    mkdir -p "$HSD_RUN" "$HSD_OUT"
    ( umask 077; od -An -N32 -tx1 /dev/urandom | tr -d ' \n' > "$HSD_RUN/key" )
    od -An -N16 -tx1 /dev/urandom | tr -d ' \n' > "$HSD_RUN/token"
    cat > "$HSD_RUN/config.toml" <<'EOF'
# The one configuration file every process of this run reads (HYPERSHELL_CONFIG_FILE), so that no
# user or system configuration where the drive runs (a database, a key) applies.
[logging]
level = "INFO"
EOF
    HYPERSHELL_CONFIG_FILE="$HSD_RUN/config.toml"
    HSD_RUN_ID_BASE=$HSD_RUN_ID
    export HSD_RUN HSD_OUT HYPERSHELL_CONFIG_FILE HSD_VARIANT="$2"
    HSD_RUN_ID=$run_id; export HSD_RUN_ID
    "$HSD_PY" "$drive/relay_probe.py" workload

    : > "$HSD_RUN/watchers"
    index=0
    for site in A B; do
        eval "members=\$HSD_SITE_${site}_NODES"
        first=1
        for node in $members; do
            index=$((index + 1))
            echo "$site $index" >> "$HSD_RUN/watchers"
            start_role "$node" "watch-$site-$index" watch "$site" "$index" "$first"
            first=0
        done
    done
    start_role "$HSD_UPSTREAM_NODE" upstream upstream
    for site in A B; do
        eval "members=\$HSD_SITE_${site}_NODES"
        set -- $members
        start_role "$1" "relay-$site" relay "$site"
    done

    status=0
    wait_roles $((HSD_TIMEOUT + 420)) || status=1
    if [ "$HSD_MODE" = local ]; then
        left=$("$HSD_PY" "$drive/relay_probe.py" sweep)
        echo "relay_drive: $run_id: swept $left leftover processes" >&2
    fi
    "$HSD_PY" "$drive/relay_judge.py" "$HSD_OUT" || status=1
    echo "relay_drive: $run_id: $([ "$status" = 0 ] && echo PASS || echo FAIL); evidence in $HSD_OUT" >&2
    HSD_RUN_ID=$HSD_RUN_ID_BASE
    return "$status"
}

start=$(date +%s)
overall=0; k=0
for variant in $HSD_RUNS; do
    k=$((k + 1))
    if [ $(( $(date +%s) - start + HSD_TIMEOUT + 120 )) -gt "$HSD_BUDGET" ]; then
        echo "relay_drive: skipping run $k ($variant): it could end past the $HSD_BUDGET s budget" >&2
        overall=1
        continue
    fi
    run_one "$k" "$variant" || overall=1
done
echo "relay_drive: $([ "$overall" = 0 ] && echo PASS || echo FAIL) in $(( $(date +%s) - start )) s; evidence under $HSD_RESULTS" >&2
exit "$overall"
