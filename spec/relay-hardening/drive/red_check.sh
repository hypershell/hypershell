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
# the editable install; `hs` subprocesses inherit it.
#
# A test is RED only when it fails for what it checks: pytest reports it failed (exit status 1), and
# its first failure (recorded by red_outcome.py, loaded as a pytest plugin) is in the test's call,
# not in collection, setup or teardown, and is an assertion (`assert` or `pytest.fail`), or else the
# exception its line names. Anything else is NOT RED: a test that passed, was skipped or was not
# collected, and one that failed on an import, a missing attribute, a wrong argument or any other
# error, as on a base build without the API the test calls. A test that fails on the base build in
# its call by an assertion is RED whatever made the assertion fail, so what each test asserts must
# be about the defect; the printed line names the failure's kind for a reader to check.
#
# red.txt holds one pytest node id per line; blank lines and lines starting with `#` are ignored.
# A line may end in ` raises=NAME`: the test is then RED when it fails by an exception of class
# NAME (or a subclass), the defect's own error, rather than by an assertion. A line prefixed `pg:`
# needs a server database: it runs only when HS_TEST_POSTGRES is set, with HS_TEST_TIMESCALE as the
# caller has it, and is otherwise skipped with a note. One line is printed per test. The exit status
# is 0 only when every test that ran is RED (and when there is nothing to check). The tree is never
# edited; the export is left in place, and its path printed.

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
outcome=$(mktemp "${TMPDIR:-/tmp}/red_outcome.XXXXXX") || exit 2

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
    expect=
    case $node in
        *' raises='*) expect=${node##* raises=}; node=${node% raises=*} ;;
    esac
    ran=$((ran + 1))
    : > "$outcome"
    RED_OUTCOME="$outcome" PYTHONPATH="$src:$here" \
        uv run --quiet pytest -q -p no:cacheprovider -p red_outcome "$node" </dev/null >/dev/null 2>&1
    status=$?
    first=$(head -n 1 "$outcome")
    phase=$(printf '%s' "$first" | cut -f 1)
    kind=$(printf '%s' "$first" | cut -f 2)
    names=$(printf '%s' "$first" | cut -f 3)
    error=$(printf '%s' "$names" | cut -d , -f 2)
    if [ "$status" -ne 1 ]; then
        echo "NOT RED  $node (pytest exit $status)"
    elif [ -z "$first" ]; then
        echo "NOT RED  $node (no failure recorded)"
    elif [ "$phase" != call ]; then
        echo "NOT RED  $node (failed in $phase: $error)"
    elif [ -n "$expect" ]; then
        case $names in
            *",$expect,"*) echo "RED      $node ($error)"; red=$((red + 1)) ;;
            *) echo "NOT RED  $node ($error, not $expect)" ;;
        esac
    elif [ "$kind" = assertion ]; then
        echo "RED      $node ($error)"
        red=$((red + 1))
    else
        echo "NOT RED  $node ($error, not an assertion)"
    fi
done <<EOF
$tests
EOF

echo "red_check: $red of $ran red, $skipped skipped"
[ "$red" -eq "$ran" ]
