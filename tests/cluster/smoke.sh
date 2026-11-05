#!/bin/sh
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0
#
# Local rehearsal of the multi-node drive: build the archive for HEAD (label 'feature'), then run
# one server and two clients as separate "nodes" (their own HSD_LOCAL under the work directory)
# sharing one results directory, with TLS on and off. Checks that compare.py finds identical,
# clean results and that no host or user name appears in the results.
#
#   HSD_SMOKE_BASELINE   also build and run this ref as the baseline (e.g. b819de6)
#   HSD_SMOKE_TASKS      task count per run (default: 2000)
#   HSD_SMOKE_DIR        work directory (default: $TMPDIR/hsd-smoke); archives are cached there
#   HSD_SMOKE_KEEP       1 = keep the results and node directories after a pass
#
# Run from the repository root:  sh tests/cluster/smoke.sh
set -eu

repo=$(git rev-parse --show-toplevel)
here="$repo/tests/cluster"
work=${HSD_SMOKE_DIR:-${TMPDIR:-/tmp}/hsd-smoke}
tasks=${HSD_SMOKE_TASKS:-2000}
mkdir -p "$work/archives"
results=$(mktemp -d "$work/results.XXXXXX")
nodes=$(mktemp -d "$work/nodes.XXXXXX")

pyrun() { uv run --quiet --no-project python "$@"; }

archive() {  # label ref -> path of a cached archive for that commit
    revision=$(git -C "$repo" rev-parse --short=12 "$2^{commit}")
    out="$work/archives/$1-$revision.tar.gz"
    [ -f "$out" ] || HSA_REF="$2" HSA_LABEL="$1" HSA_OUT="$out" sh "$here/build_archive.sh"
    echo "$out"
}

drive_once() {  # archive tls
    run_id="smoke-$(basename "$1" .tar.gz)-tls$2"
    port=$(pyrun -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
    export HSD_ARCHIVE="$1" HSD_RESULTS="$results" HSD_RUN_ID="$run_id" HSD_TLS="$2" HSD_PORT="$port" \
        HSD_AUTH="smoke-$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')" HSD_HOST=localhost HSD_BIND=127.0.0.1 \
        HSD_TASKS="$tasks" HSD_CLIENTS=2 HSD_THREADS=2 HSD_IDLE=60 HSD_TIMEOUT=600 HSD_WAIT=120
    HSD_ROLE=server HSD_LOCAL="$nodes/server" sh "$here/drive.sh" & server=$!
    HSD_ROLE=client HSD_LOCAL="$nodes/client-0" HSD_CLIENT_INDEX=0 sh "$here/drive.sh" & client0=$!
    HSD_ROLE=client HSD_LOCAL="$nodes/client-1" HSD_CLIENT_INDEX=1 sh "$here/drive.sh" & client1=$!
    rc=0
    wait "$server" || rc=$?
    wait "$client0" || rc=$?
    wait "$client1" || rc=$?
    echo "smoke: $run_id exit $rc" >&2
    return "$rc"
}

candidate=$(archive feature HEAD)
set -- "$candidate"
baseline=feature
if [ -n "${HSD_SMOKE_BASELINE:-}" ]; then
    set -- "$(archive "$HSD_SMOKE_BASELINE" "$HSD_SMOKE_BASELINE")" "$@"
    baseline=$HSD_SMOKE_BASELINE
fi

status=0
for tls in 1 0; do
    for each in "$@"; do
        drive_once "$each" "$tls" || status=1
    done
done

pyrun "$here/compare.py" "$results" --baseline "$baseline" --candidate feature --results-only || status=1

host_long=$(hostname); host_short=$(hostname | cut -d. -f1); user=${USER:-$(id -un)}
if grep -rIl -e "$host_long" -e "$host_short" -e "$user" "$results"; then
    echo "smoke: host or user name found in the results above" >&2
    status=1
fi
if [ -n "$(find "$results" -name '*.log.gz')" ]; then
    echo "smoke: failure logs in $results" >&2
    status=1
fi

if [ "$status" = 0 ]; then
    [ "${HSD_SMOKE_KEEP:-0}" = 1 ] || rm -rf "$nodes" "$results"
    echo "smoke: PASS" >&2
else
    echo "smoke: FAIL (results kept in $results, nodes in $nodes)" >&2
fi
exit "$status"
