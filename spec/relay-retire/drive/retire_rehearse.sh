#!/bin/sh
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0
#
# Local rehearsal of the multi-node relay-retire drive: retire_cluster.sh on this one machine, with
# four named stand-in nodes and the cluster's own knobs (heartrate 2 s, tasks of 40 s, eviction
# longer than the run), then the judge with the retire scenario required, then the same judge over a
# copy of the record broken on purpose (A exits with status 1 and one of its identities is evicted),
# which it must FAIL. Built from the relay-topology rehearsal (topology_rehearse.sh, that cycle's
# record).
#
# A stand-in srun, written to the scratch directory and never committed, runs each job step here:
# it ignores every flag, names the step's node from --nodelist (SLURMD_NODENAME, which the recorder
# reads), and rewrites a site client's -H to loopback, since this host's own name need not resolve.
# Servers bind and dial loopback (HSD_LOCAL=1).
#
#   HS                 the hs executable to drive (default: the project's, from `uv run`)
#   HSD_REHEARSE_DIR   parent of the scratch directory (default: TMPDIR or /tmp)
#   HSD_EVIDENCE       a directory: when set, rehearsal.txt (the drive's output), judge.txt (the
#                      judge's), judge-broken.txt (the judge's over the broken copy) and the record,
#                      rehearsal-retire.json, are written there, with host, user, key and paths replaced
#
# Nothing is deleted: the scratch directory is printed for inspection.
# Run from the repository root:  sh spec/relay-retire/drive/retire_rehearse.sh
set -eu

repo=$(git rev-parse --show-toplevel)
here="$repo/spec/relay-retire/drive"
if [ -z "${HS:-}" ]; then
    HS=$(cd "$repo" && uv run --quiet python -c 'import os, sys; print(os.path.join(os.path.dirname(sys.executable), "hs"))')
fi
py=$(dirname "$HS")/python
parent=${HSD_REHEARSE_DIR:-${TMPDIR:-/tmp}}
mkdir -p "$parent"
scratch=$(cd -P "$(mktemp -d "$parent/retire-rehearsal.XXXXXX")" && pwd)
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
export HSD_SCRUB="$(hostname) $(hostname | cut -d. -f1) ${USER:-$(id -un)} $scratch $repo"

status=0
sh "$here/retire_cluster.sh" > "$scratch/rehearsal.out" 2>&1 || status=1
cat "$scratch/rehearsal.out"
record="$HSD_RESULTS/rehearsal-retire.json"
"$py" "$here/retire_judge.py" "$HSD_RESULTS" --require retire > "$scratch/judge.out" 2>&1 || status=1
cat "$scratch/judge.out"

# The judge must fail a broken record: A exited with status 1, and one of its identities was evicted.
mkdir -p "$scratch/broken"
"$py" - "$record" "$scratch/broken/rehearsal-retire-broken.json" <<'EOF'
import sys, json
record = json.load(open(sys.argv[1]))
record['run_id'] += '-broken'
record['relays']['A']['rc'] = 1
record['server']['evicted'].append({'relay': 'A', 't': record['relays']['A']['exited_at']})
json.dump(record, open(sys.argv[2], 'w'), indent=1, sort_keys=True)
EOF
broken=0
"$py" "$here/retire_judge.py" "$scratch/broken" --require retire > "$scratch/judge-broken.out" 2>&1 || broken=$?
echo "# exit status $broken" >> "$scratch/judge-broken.out"
cat "$scratch/judge-broken.out"
[ "$broken" = 1 ] || { echo "retire_rehearse: the judge did not fail the broken record" >&2; status=1; }

if [ -n "${HSD_EVIDENCE:-}" ]; then
    mkdir -p "$HSD_EVIDENCE"
    host=$(hostname) short=$(hostname | cut -d. -f1) user=${USER:-$(id -un)}
    key=$(cat "$HSD_WORK/rehearsal-retire/key" 2>/dev/null || echo '<no key>')
    scrub() {
        sed -e "s|$scratch|<scratch>|g" -e "s|$repo|<repo>|g" -e "s|${HOME:-/nonexistent}|<home>|g" \
            -e "s|$host|<host>|g" -e "s|$short|<host>|g" -e "s|$user|<user>|g" -e "s|$key|<key>|g" "$1"
    }
    scrub "$scratch/rehearsal.out" > "$HSD_EVIDENCE/rehearsal.txt"
    scrub "$scratch/judge.out" > "$HSD_EVIDENCE/judge.txt"
    { echo "# retire_judge.py over a copy of the record with A's exit status set to 1 and an eviction of A added"
      scrub "$scratch/judge-broken.out"; } > "$HSD_EVIDENCE/judge-broken.txt"
    scrub "$record" > "$HSD_EVIDENCE/rehearsal-retire.json"
fi
echo "retire_rehearse: scratch kept in $scratch" >&2
echo "retire_rehearse: $([ "$status" = 0 ] && echo PASS || echo FAIL)" >&2
exit "$status"
