#!/bin/sh
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0
#
# Launcher for one site client of a relay in the relay drive (`hs relay --launcher`). The relay runs
# it as `sh relay_launch.sh <client command ...>`. The environment passes through untouched: what
# the site client and its tasks see is exactly what the relay left after its own scrub, which is
# what the drive checks for the upstream key.
#
#   HSD_MODE=slurm  the next node of the site (HSD_SITE_NODES, round robin under a lock in
#                   HSD_LAUNCH_STATE) gets one job step that overlaps the steps already there:
#                   $HSD_SRUN --overlap --nodes=1 --ntasks=1 --nodelist=NODE --export=ALL \
#                         $HSD_SRUN_ARGS $HSD_LEAF_SRUN_ARGS <client command ...>
#                   (--export=ALL forwards the environment whatever the site's default is.)
#   HSD_MODE=local  runs the client here, dialing the relay on loopback (-H 127.0.0.1) rather than
#                   by host name, since a host's own name need not resolve to an address its
#                   firewall lets through.
set -eu

case "${HSD_MODE:-local}" in
local)
    previous=
    for arg; do
        shift
        if [ "$previous" = "-H" ]; then set -- "$@" 127.0.0.1; else set -- "$@" "$arg"; fi
        previous=$arg
    done
    exec "$@" ;;
slurm)
    : "${HSD_SITE_NODES:?}" "${HSD_LAUNCH_STATE:?}"
    count=$(echo $HSD_SITE_NODES | wc -w | tr -d ' ')
    mkdir -p "$HSD_LAUNCH_STATE"
    i=0
    while ! mkdir "$HSD_LAUNCH_STATE/lock" 2>/dev/null; do
        i=$((i + 1)); [ "$i" -lt 600 ] || { echo "relay_launch.sh: launch lock held too long" >&2; exit 70; }
        sleep 0.1
    done
    next=$(cat "$HSD_LAUNCH_STATE/next" 2>/dev/null || echo 0)
    echo $((next + 1)) > "$HSD_LAUNCH_STATE/next"
    rmdir "$HSD_LAUNCH_STATE/lock"
    node=$(echo $HSD_SITE_NODES | tr ' ' '\n' | sed -n "$((next % count + 1))p")
    exec ${HSD_SRUN:-srun} --overlap --nodes=1 --ntasks=1 --nodelist="$node" --export=ALL \
        ${HSD_SRUN_ARGS:-} ${HSD_LEAF_SRUN_ARGS:-} "$@" ;;
*)
    echo "relay_launch.sh: HSD_MODE must be slurm or local" >&2; exit 64 ;;
esac
