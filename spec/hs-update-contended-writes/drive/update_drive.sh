#!/bin/sh
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0
#
# Drives `hs update` through the command line on a throwaway database and checks what it wrote.
# Run from the repository root:
#
#   sh spec/hs-update-contended-writes/drive/update_drive.sh sqlite
#   HS_TEST_POSTGRES=postgresql+psycopg://postgres:postgres@localhost:5432/postgres \
#       sh spec/hs-update-contended-writes/drive/update_drive.sh postgres|timescale
#
# sqlite uses a new site under $TMPDIR; postgres and timescale create the database hu_drive_<kind>
# on that server (sandbox off), initialize it with `hs initdb` under that provider, and drop it at
# the end. Each scenario starts from four new tasks:
#
#   limit      hs update --limit 2 -f group=7 color:red        both rows get both changes
#   moved      hs update -f group=7 color:blue -w group==0      every row gets both changes
#   cancel     hs update -f -R --cancel color:green             every row cancelled and tagged
#   keys       hs update color:blue size:big -f -t color:red    every row gets both keys
#   removals   hs update -f --remove-tag color size -t color:red   both names removed, shape kept
#   prompt     hs update --limit 4 -R group=9, answered only after another `hs update ID exit_status 0`
#              completed one task: 3 written, the "no longer matched" warning, exit 0
#
# Every scenario also checks that "Updated N tasks" names the rows written. One line per scenario
# (PASS or FAIL with what differed); the exit status is 0 only when every scenario passes.

set -u

kind=${1:-sqlite}
root=$(git rev-parse --show-toplevel) || exit 2
cd "$root" || exit 2
work=$(mktemp -d "${TMPDIR:-/tmp}/hu_drive.XXXXXX") || exit 2
export HYPERSHELL_SITE="$work/site" HYPERSHELL_LOGGING_LEVEL=INFO HYPERSHELL_CONFIG_FILE=
mkdir -p "$HYPERSHELL_SITE"
failed=0

admin() {  # Run one statement on the server's maintenance database with AUTOCOMMIT.
    uv run python -c 'import os, sys
from sqlalchemy import create_engine, text
engine = create_engine(os.environ["HS_TEST_POSTGRES"], isolation_level="AUTOCOMMIT")
with engine.connect() as conn:
    conn.execute(text(sys.argv[1]))
engine.dispose()' "$1"
}

case $kind in
    sqlite)
        export HYPERSHELL_DATABASE_FILE="$work/task.db" ;;
    postgres|timescale)
        : "${HS_TEST_POSTGRES:?set HS_TEST_POSTGRES}"
        db=hu_drive_$kind
        parts=$(uv run python -c 'import os
from sqlalchemy.engine import make_url
u = make_url(os.environ["HS_TEST_POSTGRES"])
print(u.host or "localhost", u.port or 5432, u.username or "postgres", u.password or "")') || exit 2
        set -- $parts
        export HYPERSHELL_DATABASE_HOST=$1 HYPERSHELL_DATABASE_PORT=$2 HYPERSHELL_DATABASE_USER=$3 \
            HYPERSHELL_DATABASE_PASSWORD=${4:-}
        export HYPERSHELL_DATABASE_PROVIDER=$kind HYPERSHELL_DATABASE_DATABASE=$db
        unset HYPERSHELL_DATABASE_FILE
        admin "DROP DATABASE IF EXISTS $db WITH (FORCE)" && admin "CREATE DATABASE $db" || exit 2
        trap 'admin "DROP DATABASE IF EXISTS $db WITH (FORCE)"' EXIT
        uv run hs initdb --yes >/dev/null 2>&1 || { echo "update_drive: hs initdb failed" >&2; exit 2; } ;;
    *)
        echo "usage: update_drive.sh sqlite|postgres|timescale" >&2; exit 2 ;;
esac

fresh() {  # Remove every task, then submit four.
    uv run hs update --delete -f >/dev/null 2>&1
    printf 'echo 0\necho 1\necho 2\necho 3\n' | uv run hs submit -f - >/dev/null 2>&1
}

count() { uv run hs list --count "$@" 2>/dev/null; }

check() {  # check NAME OUTPUT-FILE EXPECTED-UPDATED [TEST...]: each TEST is "label|actual|expected"
    name=$1 out=$2 updated=$3; shift 3
    problems=''
    grep -q "Updated $updated tasks" "$out" || problems="$problems; log: $(grep -o 'Updated [0-9]* tasks' "$out" || echo none) (want Updated $updated tasks)"
    for t in "$@"; do
        label=${t%%|*} rest=${t#*|}
        actual=${rest%%|*} expected=${rest#*|}
        [ "$actual" = "$expected" ] || problems="$problems; $label: $actual (want $expected)"
    done
    if [ -z "$problems" ]; then echo "PASS $kind $name"; else echo "FAIL $kind $name${problems}"; failed=1; fi
}

fresh; uv run hs update --limit 2 -f group=7 color:red >"$work/out" 2>&1
check limit "$work/out" 2 "group 7 + color:red|$(count -g 7 -t color:red)|2"

fresh; uv run hs update -f group=7 color:blue -w group==0 >"$work/out" 2>&1
check moved "$work/out" 4 "group 7 + color:blue|$(count -g 7 -t color:blue)|4"

fresh; uv run hs update -f -R --cancel color:green >"$work/out" 2>&1
check cancel "$work/out" 4 "cancelled + color:green|$(count -X -t color:green)|4"

fresh; uv run hs update -f color:red >/dev/null 2>&1
uv run hs update color:blue size:big -f -t color:red >"$work/out" 2>&1
check keys "$work/out" 4 "color:blue + size:big|$(count -t color:blue size:big)|4"

fresh; uv run hs update -f color:red size:big shape:x >/dev/null 2>&1
uv run hs update -f --remove-tag color size -t color:red >"$work/out" 2>&1
check removals "$work/out" 4 "shape kept|$(count -t shape:x)|4" "color left|$(count -t color)|0" \
    "size left|$(count -t size)|0"

fresh
victim=$(uv run hs list id -R --limit 1 2>/dev/null)
mkfifo "$work/answer"
(uv run hs update --limit 4 -R group=9 <"$work/answer" >"$work/out" 2>&1; echo $? >"$work/rc") &
exec 3>"$work/answer"
tries=0
until grep -q 'continue?' "$work/out" 2>/dev/null; do
    tries=$((tries + 1))
    if [ $tries -gt 600 ]; then echo "FAIL $kind prompt: no prompt within 60 s"; failed=1; break; fi
    sleep 0.1
done
uv run hs update "$victim" exit_status 0 >/dev/null 2>&1
echo yes >&3
exec 3>&-
wait
check prompt "$work/out" 3 "exit status|$(cat "$work/rc")|0" \
    "warning|$(grep -c '1 of the 4 tasks counted no longer matched' "$work/out")|1" \
    "group 9|$(count -g 9)|3" "completed task kept|$(uv run hs list group exit_status -w "id==$victim" 2>/dev/null | tr '\t' ' ' | tr -s ' ')|0 0"

echo "update_drive: work directory $work"
exit $failed
