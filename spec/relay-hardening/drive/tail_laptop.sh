#!/bin/sh
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0
#
# The end-of-run tail investigation's laptop runs: N runs of tail_run.py at the R8 size, one after
# the other, each a budget run. Run it from anywhere in the repository:
#
#   sh spec/relay-hardening/drive/tail_laptop.sh N [LABEL]
#
# LABEL (default diagnostic; [A-Za-z0-9._-]) marks the runs, as diagnostic runs or runs on a fixed
# head. The records and their log windows go to spec/relay-hardening/evidence/laptop (or
# TAIL_EVIDENCE), the run ids numbered on from the records already there (laptop-01, laptop-02, ...).
# The budget is 10 runs: it refuses N over 10, and N that would take the directory past 10 records.
# Every started run counts. The scratch sites go under TAIL_SCRATCH (default TMPDIR/relay-hardening-tail)
# and are kept: the full logs are there. HS names the hs under test (default: the project's, from
# `uv run`). Exit status: 0 when every run finished, 1 otherwise, 64 on bad usage. tail_run.py stops
# every process it starts.
set -eu

usage() { echo "tail_laptop.sh: $*" >&2; exit 64; }
n=${1:-}
label=${2:-diagnostic}
case $n in ''|*[!0-9]*) usage "N must be a whole number of runs" ;; esac
[ "$n" -ge 1 ] && [ "$n" -le 10 ] || usage "N must be 1 to 10 (the budget is 10 runs)"
case $label in *[!A-Za-z0-9._-]*|'') usage "LABEL must match [A-Za-z0-9._-]+" ;; esac

here=$(cd "$(dirname "$0")" && pwd)
repo=$(git -C "$here" rev-parse --show-toplevel)
out=${TAIL_EVIDENCE:-$repo/spec/relay-hardening/evidence/laptop}
mkdir -p "$out"
have=$(find "$out" -maxdepth 1 -name 'laptop-*.json' | wc -l | tr -d ' ')
[ $((have + n)) -le 10 ] || usage "$have records already in $out; $n more would pass the budget of 10"

if [ -z "${HS:-}" ]; then
    HS=$(cd "$repo" && uv run --quiet python -c 'import os, sys; print(os.path.join(os.path.dirname(sys.executable), "hs"))')
fi
export HS
PY=$(dirname "$HS")/python
scratch=${TAIL_SCRATCH:-${TMPDIR:-/tmp}/relay-hardening-tail}
mkdir -p "$scratch"
echo "tail_laptop: $n runs ($label), hs $HS, scratch $scratch, records $out"

status=0
i=1
while [ "$i" -le "$n" ]; do
    run_id=$(printf 'laptop-%02d' $((have + i)))
    TMPDIR=$scratch "$PY" "$here/tail_run.py" "$out" "$run_id" --label "$label" || status=1
    i=$((i + 1))
done
"$PY" "$here/tail_judge.py" "$out" || true
exit "$status"
