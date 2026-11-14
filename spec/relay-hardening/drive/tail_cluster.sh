#!/bin/sh
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0
#
# The end-of-run tail investigation's one cluster job: up to 10 runs of tail_run.py at the R8 size
# (2000 tasks of 0.2 s on 4 site clients of 8 threads, against `hs cluster -N 32`), one after the
# other, on one node of at least 36 cores (32 executors, the upstream server, the relay and the
# driver), inside an allocation that the caller holds and submits with its own options. In the
# environment contract of the relay-retire and relay-topology cluster drives (retire_cluster.sh,
# topology_cluster.sh): environment only, no scheduler directives, no submission, no site names.
#
# Each run's scratch (both runs' sites, their logs and databases) is on node-local storage and is
# copied to HSD_WORK/<run id>/run-NN before the next run starts, so the full logs survive the job.
# tail_run.py writes each run's record and log windows (host, user, home and scratch replaced) to
# HSD_RESULTS; tail_judge.py prints them at the end.
#
# Environment (nothing else is read from the caller; every HYPERSHELL_* variable is unset):
#   HS             the `hs` executable under test; `python` and `hyper-shell` must sit beside it (required)
#   HSD_WORK       work directory, on shared storage: the copied scratch     (default: ./tail-drive)
#   HSD_RESULTS    records directory: <run id>-NN.json and log windows       (default: HSD_WORK/results)
#   HSD_NODE_TMP   node-local scratch                                       (default: TMPDIR or /tmp)
#   HSD_LABEL      label of the build under test, recorded [A-Za-z0-9._-]   (default: feature)
#   HSD_RUN_ID     run id prefix [A-Za-z0-9._-]; run NN's id is <prefix>-NN (default: tail-<date>-<time>)
#   HSD_RUNS       runs, 1 to 10                                             (default: 10)
# Rehearsal only (a cluster run sets none of them): HSD_MIN_CORES (36), and a reduced size,
# HSD_TASKS, HSD_SLEEP, HSD_CLIENTS, HSD_THREADS (tail_run.py's defaults are the R8 size).
#
# The run in progress is stopped on exit, on error and on a signal (SIGTERM to tail_run.py, which
# stops every process it started), by its recorded pid. Nothing is deleted. Exit status: 0 if every
# run finished (and, at the R8 size, the records pass tail_judge.py's budget check), 1 otherwise,
# 64 on bad usage.
set -eu

drive=$(cd -P "$(dirname "$0")" && pwd)
usage() { echo "tail_cluster.sh: $*" >&2; exit 64; }

: "${HS:?HS must name the hs executable under test}"
[ -x "$HS" ] || usage "HS is not executable: $HS"
HSD_PY=$(dirname "$HS")/python
[ -x "$HSD_PY" ] || usage "no python beside HS: $HSD_PY"
PATH="$(dirname "$HS"):$PATH"
export HS PATH

HSD_WORK=${HSD_WORK:-$PWD/tail-drive}
HSD_RESULTS=${HSD_RESULTS:-$HSD_WORK/results}
HSD_NODE_TMP=${HSD_NODE_TMP:-${TMPDIR:-/tmp}}
HSD_LABEL=${HSD_LABEL:-feature}
HSD_RUN_ID=${HSD_RUN_ID:-tail-$(date +%Y%m%d-%H%M%S)}
HSD_RUNS=${HSD_RUNS:-10}
HSD_MIN_CORES=${HSD_MIN_CORES:-36}
case "$HSD_RUN_ID" in *[!A-Za-z0-9._-]*|'') usage "HSD_RUN_ID must match [A-Za-z0-9._-]+" ;; esac
case "$HSD_LABEL" in *[!A-Za-z0-9._-]*|'') usage "HSD_LABEL must match [A-Za-z0-9._-]+" ;; esac
case "$HSD_RUNS$HSD_MIN_CORES" in *[!0-9]*) usage "HSD_RUNS and HSD_MIN_CORES must be whole numbers" ;; esac
[ "$HSD_RUNS" -ge 1 ] && [ "$HSD_RUNS" -le 10 ] || usage "HSD_RUNS must be 1 to 10 (the budget is 10 runs)"
size=
for pair in tasks:HSD_TASKS sleep:HSD_SLEEP clients:HSD_CLIENTS threads:HSD_THREADS; do
    eval "value=\${${pair#*:}:-}"
    if [ -n "$value" ]; then size="$size --${pair%%:*} $value"; fi
done
cores=$(getconf _NPROCESSORS_ONLN 2>/dev/null || nproc 2>/dev/null || echo 0)
[ "$cores" -ge "$HSD_MIN_CORES" ] || usage "this node has $cores cores; the runs need at least $HSD_MIN_CORES"

for name in $(env | sed -n 's/^\(HYPERSHELL_[A-Z0-9_]*\)=.*/\1/p'); do unset "$name"; done
TZ=UTC
export TZ

work=$HSD_WORK/$HSD_RUN_ID
scratch=$HSD_NODE_TMP/$HSD_RUN_ID
[ ! -e "$work" ] || usage "$work exists; a run id is used once"
[ ! -e "$scratch" ] || usage "$scratch exists; a run id is used once"
mkdir -p "$work" "$scratch" "$HSD_RESULTS"

log() { echo "tail_cluster: $HSD_RUN_ID: $*" >&2; }
pid=
cleanup() {
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        kill -TERM "$pid" 2>/dev/null || true
        n=0
        while kill -0 "$pid" 2>/dev/null && [ "$n" -lt 15 ]; do sleep 1; n=$((n + 1)); done
        kill -KILL "$pid" 2>/dev/null || true
    fi
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

log "$HSD_RUNS runs on $cores cores, hs $HS, label $HSD_LABEL${size:+, size$size}"
status=0
i=1
while [ "$i" -le "$HSD_RUNS" ]; do
    run=$(printf '%s-%02d' "$HSD_RUN_ID" "$i")
    tmp=$scratch/$(printf 'run-%02d' "$i")
    mkdir -p "$tmp"
    # shellcheck disable=SC2086
    TMPDIR=$tmp "$HSD_PY" "$drive/tail_run.py" "$HSD_RESULTS" "$run" --label "$HSD_LABEL" $size &
    pid=$!
    if wait "$pid"; then result=0; else result=$?; status=1; fi
    pid=
    mkdir -p "$work/$(basename "$tmp")"
    cp -R "$tmp/." "$work/$(basename "$tmp")/" || { log "could not copy $tmp to $work"; status=1; }
    log "run $run: exit $result; scratch copied to $work/$(basename "$tmp")"
    i=$((i + 1))
done
if [ -z "$size" ]; then
    "$HSD_PY" "$drive/tail_judge.py" --budget 10 "$HSD_RESULTS" || status=1
else
    "$HSD_PY" "$drive/tail_judge.py" "$HSD_RESULTS" || true
fi
log "$([ "$status" = 0 ] && echo PASS || echo FAIL); records in $HSD_RESULTS"
exit "$status"
