#!/bin/sh
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0
#
# Proves that the tests in mutant-red.txt pin the retry mark's own row condition. This tree's `src`
# (MUTANT_REV, default HEAD) is exported with `git archive` into a new directory under $TMPDIR, the
# 'retryable' condition's UPDATE is made to match its row by id alone (`match = (by_id, )`), and
# spec/relay-hardening/drive/red_check.sh runs each listed test against that export. Run it from the
# repository root:
#
#   sh spec/contended-writes/drive/mutant_check.sh
#
# Set HS_TEST_POSTGRES (and HS_TEST_TIMESCALE) for the `pg:` line. The exit status is red_check's:
# 0 only when every test that ran is red on the mutant; 2 when the mutation no longer applies.

set -u

here=$(cd "$(dirname "$0")/.." && pwd)
root=$(git -C "$here" rev-parse --show-toplevel) || exit 2
rev=${MUTANT_REV:-HEAD}
tmp=${TMPDIR:-/tmp}
work=$(mktemp -d "${tmp%/}/mutant_check.XXXXXX") || exit 2
git -C "$root" archive "$rev" src | tar -x -C "$work" || exit 2

model=$work/src/hypershell/data/model.py
line='            match, lookup, read = awaiting, awaiting, table.c.id'
if [ "$(grep -c -x -F "$line" "$model")" -ne 1 ]; then
    echo "mutant_check: the 'retryable' match line is not in $model once" >&2
    exit 2
fi
sed -e 's/^            match, lookup, read = awaiting, awaiting, table\.c\.id$/            match, lookup, read = (by_id, ), awaiting, table.c.id/' \
    "$model" > "$model.mutant" && mv "$model.mutant" "$model" || exit 2
if [ "$(grep -c -x -F '            match, lookup, read = (by_id, ), awaiting, table.c.id' "$model")" -ne 1 ]; then
    echo "mutant_check: the mutation did not apply" >&2
    exit 2
fi
echo "mutant_check: $rev with the retry mark matching by id alone"

RED_BASE_REV="$rev (mutant)" RED_BASE_SRC="$work/src" RED_LIST="$here/mutant-red.txt" \
    sh "$root/spec/relay-hardening/drive/red_check.sh"
