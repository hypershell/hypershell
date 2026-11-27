#!/bin/sh
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0
#
# Local rehearsal of the multi-node topology drive: topology_cluster.sh on this one machine, with
# four named stand-in nodes, both scenarios scaled down (1 s checks and heartbeats, 12 s eviction,
# 1 s tasks, fewer of them), then the judge with both scenarios required.
#
# A stand-in srun, written to the scratch directory and never committed, runs each job step here:
# it ignores every flag, names the step's node from --nodelist (SLURMD_NODENAME, which the recorder
# reads), and rewrites a site client's -H to loopback, since this host's own name need not resolve.
# Servers bind and dial loopback (HSD_LOCAL=1).
#
#   HS                 the hs executable to drive (default: the project's, from `uv run`)
#   HSD_REHEARSE_DIR   parent of the scratch directory (default: TMPDIR or /tmp)
#   HSD_EVIDENCE       a directory: when set, rehearsal.txt (the drive's output) and judge.txt (the
#                      judge's) are written there, with host, user, key and paths replaced
#
# Nothing is deleted: the scratch directory is printed for inspection.
# Run from the repository root:  sh spec/relay-topology/drive/topology_rehearse.sh
set -eu

repo=$(git rev-parse --show-toplevel)
here="$repo/spec/relay-topology/drive"
if [ -z "${HS:-}" ]; then
    HS=$(cd "$repo" && uv run --quiet python -c 'import os, sys; print(os.path.join(os.path.dirname(sys.executable), "hs"))')
fi
parent=${HSD_REHEARSE_DIR:-${TMPDIR:-/tmp}}
mkdir -p "$parent"
scratch=$(cd -P "$(mktemp -d "$parent/topology-rehearsal.XXXXXX")" && pwd)
mkdir -p "$scratch/bin" "$scratch/node-tmp"
cat > "$scratch/bin/srun" <<'EOF'
#!/bin/sh
# Stand-in srun for the rehearsal: the step runs here; resource flags are ignored.
node=
while [ $# -gt 0 ]; do
    case $1 in
        --nodelist=*) node=${1#--nodelist=}; shift ;;
        -*) shift ;;
        *) break ;;
    esac
done
prev=
for arg; do
    shift
    if [ "$prev" = -H ]; then set -- "$@" 127.0.0.1; else set -- "$@" "$arg"; fi
    prev=$arg
done
SLURMD_NODENAME=$node
export SLURMD_NODENAME
exec "$@"
EOF
chmod +x "$scratch/bin/srun"

export HS HSD_LOCAL=1 HSD_SRUN="$scratch/bin/srun" HSD_NODES="node-0 node-1 node-2 node-3"
export HSD_WORK="$scratch/work" HSD_RESULTS="$scratch/results" HSD_NODE_TMP="$scratch/node-tmp"
export HSD_RUN_ID=rehearsal HSD_LABEL=rehearsal
export HSD_PERIOD=1 HSD_EVICT=12 HSD_HEARTRATE=1 HSD_CLIENT_TIMEOUT=4 HSD_BUNDLEWAIT=1 HSD_RELAY_PERIOD=1 \
    HSD_SLEEP=1 HSD_TASKS=160 HSD_SIDE_TASKS=6 HSD_CPU_MAX=5 HSD_SIDE_MAX=2 HSD_FILL_TASKS=90 HSD_FILL_MAX=3 \
    HSD_QUIET=8 HSD_POLL=1 HSD_TIMEOUT=420 HSD_BUDGET=1200
export HSD_SCRUB="$(hostname) $(hostname | cut -d. -f1) ${USER:-$(id -un)} $scratch $repo"

status=0
sh "$here/topology_cluster.sh" > "$scratch/rehearsal.out" 2>&1 || status=1
cat "$scratch/rehearsal.out"
"$(dirname "$HS")/python" "$here/topology_judge.py" "$HSD_RESULTS" --require spread-kill,fill \
    > "$scratch/judge.out" 2>&1 || status=1
cat "$scratch/judge.out"

if [ -n "${HSD_EVIDENCE:-}" ]; then
    mkdir -p "$HSD_EVIDENCE"
    host=$(hostname) short=$(hostname | cut -d. -f1) user=${USER:-$(id -un)}
    scrub() {
        sed -e "s|$scratch|<scratch>|g" -e "s|$repo|<repo>|g" -e "s|${HOME:-/nonexistent}|<home>|g" \
            -e "s|$host|<host>|g" -e "s|$short|<host>|g" -e "s|$user|<user>|g" "$1"
    }
    scrub "$scratch/rehearsal.out" > "$HSD_EVIDENCE/rehearsal.txt"
    scrub "$scratch/judge.out" > "$HSD_EVIDENCE/judge.txt"
fi
echo "topology_rehearse: scratch kept in $scratch" >&2
echo "topology_rehearse: $([ "$status" = 0 ] && echo PASS || echo FAIL)" >&2
exit "$status"
