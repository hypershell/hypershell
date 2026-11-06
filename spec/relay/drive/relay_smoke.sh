#!/bin/sh
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0
#
# Local rehearsal of the relay drive: relay_drive.sh with HSD_MODE=local on this one machine. Three
# local "nodes" (their own node-local directories) stand in for the allocation: node 0 runs the
# upstream server, nodes 1 and 2 are sites A and B, each with its relay and its watcher. The
# launcher wrapper starts site clients here, on loopback, with the relay's environment untouched.
# A small workload: 300 tasks in 3 groups, one failing on its first attempt, upstream eviction at
# 10 s, relay heartbeat 1 s, relay B killed at 40 % of rows completed. Then the judge, and a scan of
# the records for this host's name, the user's name, and each run's key.
#
#   HS                 the hs executable to drive (default: the project's, from `uv run`)
#   HSD_SMOKE_RUNS     variants to rehearse (default: kill)
#   HSD_SMOKE_DIR      parent of the scratch directory (default: TMPDIR or /tmp)
#
# Nothing is deleted: the scratch directory (records, logs, transport) is printed for inspection.
# Run from the repository root:  sh spec/relay/drive/relay_smoke.sh
set -eu

repo=$(git rev-parse --show-toplevel)
here="$repo/spec/relay/drive"
if [ -z "${HS:-}" ]; then
    HS=$(cd "$repo" && uv run --quiet python -c 'import os, sys; print(os.path.join(os.path.dirname(sys.executable), "hs"))')
fi
parent=${HSD_SMOKE_DIR:-${TMPDIR:-/tmp}}
mkdir -p "$parent"
work=$(mktemp -d "$parent/relay-smoke.XXXXXX")

export HS HSD_MODE=local HSD_WORK="$work/work" HSD_RESULTS="$work/results" HSD_RUN_ID=smoke HSD_LABEL=smoke
export HSD_RUNS="${HSD_SMOKE_RUNS:-kill}" HSD_TASKS=300 HSD_FAILING=1 HSD_GROUPS=3 HSD_SLEEP=0.2 \
    HSD_EVICT=10 HSD_HEARTRATE=1 HSD_THREADS=2 HSD_CLIENTS_PER_NODE=2 HSD_SAMPLE=0.5 HSD_SETTLE=3 \
    HSD_TIMEOUT=240 HSD_BUDGET=1800 HSD_PAUSE=20

status=0
sh "$here/relay_drive.sh" || status=1

host_long=$(hostname); host_short=$(hostname | cut -d. -f1); user=${USER:-$(id -un)}
if grep -rIl -e "$host_long" -e "$host_short" -e "$user" "$work/results"; then
    echo "relay_smoke: host or user name found in the records above" >&2
    status=1
fi
for key in "$work"/work/*/key; do
    [ -f "$key" ] || continue
    if grep -rIl -F -e "$(cat "$key")" "$work/results"; then
        echo "relay_smoke: an upstream key found in the records above" >&2
        status=1
    fi
done
records=$(find "$work/results" -name '*.json' | wc -l | tr -d ' ')
echo "relay_smoke: $records records; scratch kept in $work" >&2
echo "relay_smoke: $([ "$status" = 0 ] && echo PASS || echo FAIL)" >&2
exit "$status"
