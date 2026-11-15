#!/bin/sh
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0
#
# Runs pytest with every test listed in red.txt deselected, so a tests-first commit can show that
# everything it does not expect to fail still passes. Run it from the repository root:
#
#   sh spec/contended-writes/drive/rest_green.sh [PYTEST ARGS...]
#
# With no arguments the whole suite runs. red.txt is the list spec/relay-hardening/drive/red_check.sh
# reads (one node id per line; `#` comments; a `pg:` prefix and a ` raises=NAME` suffix are
# stripped here). RED_LIST names another list. The exit status is pytest's.

set -u

here=$(cd "$(dirname "$0")/.." && pwd)
list=${RED_LIST:-$here/red.txt}
if [ ! -f "$list" ]; then
    echo "rest_green: no list at $list" >&2
    exit 2
fi

set -- -q -p no:cacheprovider "$@"
nodes=$(sed -e 's/[[:space:]]*$//' -e '/^[[:space:]]*#/d' -e '/^[[:space:]]*$/d' \
            -e 's/^pg://' -e 's/ raises=.*$//' "$list")
count=0
for node in $nodes; do
    set -- "$@" --deselect "$node"
    count=$((count + 1))
done
echo "rest_green: $count listed test(s) deselected"
uv run pytest "$@"
