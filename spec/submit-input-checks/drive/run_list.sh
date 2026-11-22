#!/bin/sh
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0
#
# Run the pytest node ids listed in a file, in one bounded, serial pytest run, from the repository root:
#
#   sh spec/submit-input-checks/drive/run_list.sh spec/submit-input-checks/drive/guards.txt [SECONDS]
#
# One node id per line; blank lines and lines starting with `#` are ignored. A line prefixed `pg:` runs
# only when HS_TEST_POSTGRES is set. Node ids are passed as arguments without shell globbing, so ids
# holding brackets ([sqlite]) are safe under any shell. Exits with pytest's status (124 past the bound).

set -u

here=$(cd "$(dirname "$0")" && pwd)
list=${1:?usage: run_list.sh LIST [SECONDS]}
bound=${2:-1800}

set --
while IFS= read -r line || [ -n "$line" ]; do
    line=$(printf '%s' "$line" | sed -e 's/[[:space:]]*$//')
    case $line in
        ''|'#'*) continue ;;
        pg:*)
            [ -n "${HS_TEST_POSTGRES:-}" ] || { echo "SKIP ${line#pg:} (needs HS_TEST_POSTGRES)"; continue; }
            line=${line#pg:}
            ;;
    esac
    set -- "$@" "$line"
done < "$list"

if [ "$#" -eq 0 ]; then
    echo "run_list: nothing to run in $list"
    exit 0
fi
exec python3 "$here/bounded.py" "$bound" uv run pytest -q -p no:cacheprovider "$@"
