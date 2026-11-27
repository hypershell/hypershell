#!/bin/sh
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0
#
# A relay's launcher in the multi-node topology drive (`[relay] launcher`):
#
#   launcher = "sh topology_launch.sh {zone}"
#
# The relay runs it as `sh topology_launch.sh WORD <client command ...>`. It picks the next of the
# site's client nodes (HSD_CLIENT_NODES, round robin under a lock in HSD_LAUNCH_STATE) and starts the
# client there as one job step of the scheduler's own srun, wrapped by the recorder:
#
#   $HSD_SRUN --overlap --nodes=1 --ntasks=1 --nodelist=NODE --export=ALL $HSD_SRUN_ARGS \
#       sh topology_record.sh T_LAUNCH WORD <client command ...>
#
# --overlap lets the step share the allocation's resources with the steps already on that node, and
# --export=ALL forwards the environment whatever the site's default is: a site client's key arrives
# there, never on its command line, and the recorder checks the upstream key is not there too.
# T_LAUNCH is the time here, when the relay asked for the client.
set -eu

: "${HSD_CLIENT_NODES:?}" "${HSD_LAUNCH_STATE:?}" "${HSD_DRIVE:?}"
t=$(date +%s.%N 2>/dev/null)
case $t in *N*|'') t=$("${HSD_PY:-python3}" -c 'import time; print(f"{time.time():.3f}")') ;; esac

mkdir -p "$HSD_LAUNCH_STATE"
i=0
while ! mkdir "$HSD_LAUNCH_STATE/lock" 2>/dev/null; do
    i=$((i + 1)); [ "$i" -lt 600 ] || { echo "topology_launch.sh: launch lock held too long" >&2; exit 70; }
    sleep 0.1
done
next=$(cat "$HSD_LAUNCH_STATE/next" 2>/dev/null || echo 0)
echo $((next + 1)) > "$HSD_LAUNCH_STATE/next"
rmdir "$HSD_LAUNCH_STATE/lock"
set -- "$t" "$@"
count=$(echo $HSD_CLIENT_NODES | wc -w | tr -d ' ')
node=$(echo $HSD_CLIENT_NODES | tr ' ' '\n' | sed -n "$((next % count + 1))p")
exec ${HSD_SRUN:-srun} --overlap --nodes=1 --ntasks=1 --nodelist="$node" --export=ALL ${HSD_SRUN_ARGS:-} \
    sh "$HSD_DRIVE/topology_record.sh" "$@"
