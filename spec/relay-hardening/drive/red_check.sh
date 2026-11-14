#!/bin/sh
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0
#
# Proves that each regression test listed in red.txt fails on the base build. Run it from anywhere
# in the repository:
#
#   sh spec/relay-hardening/drive/red_check.sh
#
# The base build's `src` (RED_BASE_REV, default 2ffb5cc) is exported with `git archive` into a new
# directory under $TMPDIR, unless RED_BASE_SRC names an exported `src` to use instead. Each listed
# test then runs on its own, from this tree's tests, with that `src` first on PYTHONPATH, ahead of
# the editable install; `hs` subprocesses inherit it. A test is RED when pytest reports it failed
# (exit status 1); anything else (passed, skipped, not collected, an error) is NOT RED.
#
# red.txt holds one pytest node id per line; blank lines and lines starting with `#` are ignored.
# A line prefixed `pg:` needs a server database: it runs only when HS_TEST_POSTGRES is set, with
# HS_TEST_TIMESCALE as the caller has it, and is otherwise skipped with a note. One line is printed
# per test. The exit status is 0 only when every test that ran is RED (and when there is nothing to
# check). The tree is never edited; the export is left in place, and its path printed.

set -u

here=$(cd "$(dirname "$0")" && pwd)
root=$(git -C "$here" rev-parse --show-toplevel) || exit 2
list=${RED_LIST:-$here/red.txt}
rev=${RED_BASE_REV:-2ffb5cc}

if [ ! -f "$list" ]; then
    echo "red_check: no list at $list" >&2
    exit 2
fi
tests=$(sed -e 's/[[:space:]]*$//' -e '/^[[:space:]]*#/d' -e '/^[[:space:]]*$/d' "$list")
if [ -z "$tests" ]; then
    echo "red_check: nothing to check in $list"
    exit 0
fi

src=${RED_BASE_SRC:-}
if [ -z "$src" ]; then
    tmp=${TMPDIR:-/tmp}
    work=$(mktemp -d "${tmp%/}/red_check.XXXXXX") || exit 2
    git -C "$root" archive "$rev" src | tar -x -C "$work" || exit 2
    src=$work/src
fi
echo "red_check: base $rev, source $src"

cd "$root" || exit 2

# The base source must be the one imported, or every verdict below would be about this tree.
imported=$(PYTHONPATH="$src" uv run --quiet python -c 'import hypershell; print(hypershell.__file__)') || exit 2
case $imported in
    "$src"/*) ;;
    *) echo "red_check: hypershell imported from $imported, not from $src" >&2; exit 2 ;;
esac

ran=0
red=0
skipped=0
while IFS= read -r line; do
    case $line in
        pg:*)
            node=${line#pg:}
            if [ -z "${HS_TEST_POSTGRES:-}" ]; then
                echo "SKIP     $node (needs HS_TEST_POSTGRES)"
                skipped=$((skipped + 1))
                continue
            fi
            ;;
        *) node=$line ;;
    esac
    ran=$((ran + 1))
    PYTHONPATH="$src" uv run --quiet pytest -q -p no:cacheprovider "$node" </dev/null >/dev/null 2>&1
    status=$?
    if [ "$status" -eq 1 ]; then
        echo "RED      $node"
        red=$((red + 1))
    else
        echo "NOT RED  $node (pytest exit $status)"
    fi
done <<EOF
$tests
EOF

echo "red_check: $red of $ran red, $skipped skipped"
[ "$red" -eq "$ran" ]
