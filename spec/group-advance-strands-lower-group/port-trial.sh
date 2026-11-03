#!/bin/sh
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0
#
# Trial port of this cycle onto 2.8.3, after #68, path-limited to src/ and tests/ (GOAL R9).
#
# Run from the root of this cycle's worktree, on its branch, after the fix has landed:
#   sh spec/group-advance-strands-lower-group/port-trial.sh
#
# It makes a detached worktree of origin/master next to the other worktrees and applies #68's
# src/ and tests/ diff. #68 has one known conflict on 2.8.3, the stop branch of
# Scheduler.load_bundle, which is resolved here by script. It then applies this cycle's src/
# and tests/ diff, which must apply with no conflict, and runs the group tests under a kill
# timer. The worktree is removed on every exit path except SIGKILL. No branch moves, and
# master is checked unchanged at the end.
set -eu

base=b31c66e    # This cycle's base on develop.
pr68=b819de6    # The group-advance-retry-gate fix (#68), which 2.8.4 takes first.

# Run a command in its own session and kill its process group after $1 seconds (exit 124).
kt() {
    python3 -c 'import os,signal,subprocess,sys,threading;p=subprocess.Popen(sys.argv[2:],start_new_session=True);t=threading.Timer(float(sys.argv[1]),os.killpg,(p.pid,signal.SIGKILL));t.start();r=p.wait();t.cancel();sys.exit(124 if r<0 else r)' "$@"
}

root="$(pwd)"
common="$(git rev-parse --path-format=absolute --git-common-dir)"
trial="$(dirname "$common")/.worktrees/port-2.8.4-trial"
master_before="$(git rev-parse --verify master)"
if [ -e "$trial" ]; then
    echo "port-trial: $trial already exists; remove it with 'git worktree remove --force' first" >&2
    exit 2
fi

work="$(mktemp -d "${TMPDIR:-/tmp}/port-trial.XXXXXX")"
git diff "$pr68^" "$pr68" -- src tests > "$work/68.diff"
git diff "$base" HEAD -- src tests > "$work/cycle.diff"

cleanup() {
    cd "$root"
    git worktree remove --force "$trial" 2>/dev/null || true
    git worktree prune
}
trap cleanup EXIT
trap 'exit 130' INT TERM

git worktree add --detach "$trial" origin/master
cd "$trial"

echo '--- apply #68 (one known conflict expected)'
if git apply -3 "$work/68.diff"; then
    echo "port-trial: #68 applied without its known conflict; re-check the resolution" >&2
    exit 3
fi
conflicted="$(git diff --name-only --diff-filter=U)"
if [ "$conflicted" != "src/hypershell/server.py" ]; then
    echo "port-trial: unexpected conflicts after #68: $conflicted" >&2
    exit 3
fi
python3 - src/hypershell/server.py <<'PY'
import re, sys
path = sys.argv[1]
text = open(path).read()
resolved = (
    "        elif (not self.forever_mode and Task.count() > 0 and Task.count_remaining() == 0\n"
    "              and Task.count_retryable(self.attempts, group=self.current_group, eager=self.eager) == 0\n"
    "              and not self.startup_phase):\n"
    "            # A failure written back after Task.next() is still retried before the run stops;\n"
    "            # retryable failures are counted after incomplete tasks so that write is always seen.\n"
)
block = re.compile(r'^<<<<<<< .*?^>>>>>>> [^\n]*\n', re.M | re.S)
blocks = block.findall(text)
if len(blocks) != 1 or 'startup_phase' not in blocks[0] or 'count_retryable' not in blocks[0]:
    sys.exit(f'port-trial: expected one stop-branch conflict in {path}, found {len(blocks)}')
open(path, 'w').write(block.sub(lambda _: resolved, text))
PY
if grep -nE '^(<<<<<<<|=======$|>>>>>>>)' src/hypershell/server.py; then
    echo "port-trial: conflict markers remain after resolving #68" >&2
    exit 3
fi
git add src/hypershell/server.py

echo '--- apply this cycle (must be conflict-free)'
git apply -3 "$work/cycle.diff"
git status --short

echo '--- environment (2.8.3 lock)'
uv sync --frozen

echo '--- tests'
tests="tests/test_group_order.py tests/test_retry_gate.py tests/test_groups.py"
if ! kt 1200 uv run --frozen pytest -q -l $tests > "$work/pytest.log" 2>&1; then
    tail -n 60 "$work/pytest.log"
    grep -E '^(FAILED|ERROR)' "$work/pytest.log" || true
    echo '--- one rerun of the failed tests (known TLS localhost flake; record it)'
    kt 1200 uv run --frozen pytest -q -l --lf $tests
else
    tail -n 3 "$work/pytest.log"
fi

cd "$root"
if [ "$(git rev-parse --verify master)" != "$master_before" ]; then
    echo "port-trial: master moved" >&2
    exit 4
fi
echo "port-trial: OK (logs in $work)"
