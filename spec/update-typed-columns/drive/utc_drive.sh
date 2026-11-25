#!/bin/sh
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0
#
# CLI drive for update-typed-columns: runs the commands a user runs on a throwaway database and checks
# what `hs update` refuses and writes, on SQLite or PostgreSQL. Run from the worktree root:
#
#   sh spec/update-typed-columns/drive/utc_drive.sh sqlite MODE
#   HS_TEST_POSTGRES=postgresql+psycopg://postgres:postgres@localhost:5432/postgres \
#       sh spec/update-typed-columns/drive/utc_drive.sh postgres MODE
#
# sqlite uses a new site under $TMPDIR; postgres creates the database utc_drive_<mode> on that server
# (sandbox off), initializes it with `hs initdb`, and drops it on exit. Every command runs with
# TZ=America/Indiana/Indianapolis and PGTZ=UTC, so a time read in the wrong zone shows. Each mode
# submits three tasks tagged n:0, n:1 and n:2 and starts no server. One line per check; exits 0 when
# every check of the mode holds, else 1 at the first that does not (its output says which).
#
#   numbers   cores=x, memory=4X, timeout=4G, exit_status=1.5, attempt=none, part=-1, cores_max=inf
#             and waited=x exit 2 naming the column with nothing written; id refused in both forms;
#             memory=4G timeout=1:30:00 and the other ranged columns stored as read; ID cores 100000
#             exits 2; cores=1025 warns once and writes (R2-R4, R8-R10, R12)
#   kinds     retried=yes, a source uuid in upper case (stored lower), zone=lab (hs list shows lab),
#             zone=default (back to the default zone); retried=2, args=none, ID submit_id none and a
#             braced uuid exit 2 (R5, R7, R8)
#   datetime  09:00 local, 14:00Z and 19:00+05:00 are one instant shown alike; now and
#             ID submit_time none exit 2; completion_time=none clears (R6, R8)
#   tags      tag=x exits 2 naming KEY:VALUE; color=blue sets a tag; exit_stauts=0 and ID Cores 4
#             exit 2 with the nearest column and the way out; exit_stauts:0 sets a tag; a b=1 exits 2;
#             the prompt lists column and tag changes (R11, R16, R17)

set -u

backend=${1:?usage: utc_drive.sh sqlite|postgres MODE}
mode=${2:?usage: utc_drive.sh sqlite|postgres numbers|kinds|datetime|tags}
root=$(git rev-parse --show-toplevel) || exit 2
cd "$root" || exit 2
work=$(mktemp -d "${TMPDIR:-/tmp}/utc_drive.XXXXXX") || exit 2
export HYPERSHELL_SITE="$work/site" HYPERSHELL_LOGGING_LEVEL=INFO HYPERSHELL_CONFIG_FILE=
export TZ=America/Indiana/Indianapolis PGTZ=UTC
mkdir -p "$HYPERSHELL_SITE"

fail() { echo "FAIL [$backend $mode] $*"; exit 1; }
pass() { echo "ok   [$backend $mode] $*"; }
hs() { uv run --quiet --project "$root" hs "$@"; }
field() { hs list "$1" -t "$2" 2>/dev/null; }

built='numbers'  # The modes built so far
case " $built " in *" $mode "*) ;; *) fail "mode $mode not built" ;; esac

admin() {  # Run one statement on the server's maintenance database with AUTOCOMMIT.
    uv run --quiet --project "$root" python -c 'import os, sys
from sqlalchemy import create_engine, text
engine = create_engine(os.environ["HS_TEST_POSTGRES"], isolation_level="AUTOCOMMIT")
with engine.connect() as conn:
    conn.execute(text(sys.argv[1]))
engine.dispose()' "$1"
}

case $backend in
    sqlite)
        export HYPERSHELL_DATABASE_FILE="$work/task.db" ;;
    postgres)
        : "${HS_TEST_POSTGRES:?set HS_TEST_POSTGRES}"
        db=utc_drive_$mode
        parts=$(uv run --quiet --project "$root" python -c 'import os
from sqlalchemy.engine import make_url
u = make_url(os.environ["HS_TEST_POSTGRES"])
print(u.host or "localhost", u.port or 5432, u.username or "postgres", u.password or "")') || exit 2
        set -- $parts
        export HYPERSHELL_DATABASE_HOST=$1 HYPERSHELL_DATABASE_PORT=$2 HYPERSHELL_DATABASE_USER=$3 \
            HYPERSHELL_DATABASE_PASSWORD=${4:-}
        export HYPERSHELL_DATABASE_PROVIDER=postgres HYPERSHELL_DATABASE_DATABASE=$db
        unset HYPERSHELL_DATABASE_FILE
        admin "DROP DATABASE IF EXISTS $db WITH (FORCE)" && admin "CREATE DATABASE $db" || exit 2
        trap 'admin "DROP DATABASE IF EXISTS $db WITH (FORCE)"' EXIT
        hs initdb --yes >/dev/null 2>&1 || { echo "utc_drive: hs initdb failed" >&2; exit 2; } ;;
    *)
        echo "usage: utc_drive.sh sqlite|postgres MODE" >&2; exit 2 ;;
esac

cd "$HYPERSHELL_SITE" || exit 2
printf 'echo 0  # HYPERSHELL: n:0\necho 1  # HYPERSHELL: n:1\necho 2  # HYPERSHELL: n:2\n' > t.in
hs submit t.in 2>/dev/null || fail 'submit'
[ "$(hs list -c 2>/dev/null)" = 3 ] || fail 'three tasks after submit'
id1=$(field id n:1)

refused() {  # refused COLUMN ARG...: exit 2, a line naming COLUMN, nothing counted or written
    column=$1; shift
    before=$(hs list -t n:1 2>/dev/null)
    hs update "$@" 2> err.txt > out.txt; status=$?
    [ "$status" -eq 2 ] || fail "hs update $* exited $status, not 2: $(cat err.txt)"
    grep -q -- "$column" err.txt || fail "hs update $*: message does not name $column: $(cat err.txt)"
    grep -q 'Traceback' err.txt && fail "hs update $*: traceback: $(cat err.txt)"
    grep -q 'Searching database' err.txt && fail "hs update $* counted before refusing: $(cat err.txt)"
    [ "$(hs list -t n:1 2>/dev/null)" = "$before" ] || fail "hs update $* wrote"
    pass "hs update $* exits 2 naming $column, nothing written"
}

stored() {  # stored COLUMN EXPECTED: the column of the task tagged n:1
    value=$(field "$1" n:1)
    [ "$value" = "$2" ] || fail "$1 is '$value', not '$2'"
}

case $mode in
numbers)
    refused 'cores: expected' cores=x -t n:1 -f
    refused 'memory: expected' memory=4X -t n:1 -f
    refused 'timeout: expected' timeout=4G -t n:1 -f
    refused 'exit_status: expected' exit_status=1.5 -t n:1 -f
    refused 'attempt: expected' attempt=none -t n:1 -f
    refused 'part: expected' part=-1 -t n:1 -f
    refused 'cores_max: expected' cores_max=inf -t n:1 -f
    refused 'waited: expected' waited=x -t n:1 -f
    refused 'cores: expected' "$id1" cores 100000
    refused 'cores: expected' args=changed cores=x color:red -t n:1 -f
    refused 'id' id=x -t n:1 -f
    refused 'id' "$id1" id x
    hs update memory=4G timeout=1:30:00 cores=4 exit_status=-1 waited=1h duration=90m attempt=3 part=2 \
        cores_max=1.5 memory_max=1024 -t n:1 -f 2> err.txt || fail "good changes exited $?: $(cat err.txt)"
    stored memory 4294967296; stored timeout 5400; stored cores 4; stored exit_status -1; stored waited 3600
    stored duration 5400; stored attempt 3; stored part 2; stored cores_max 1.5; stored memory_max 1024.0
    pass 'memory=4G timeout=1:30:00 and the other ranged columns stored as read'
    hs update "$id1" memory 512MB 2> err.txt || fail "ID memory 512MB exited $?: $(cat err.txt)"
    stored memory 536870912
    pass 'hs update ID memory 512MB stores 536870912'
    hs update cores=1025 -t n:1 -f 2> err.txt || fail "cores=1025 exited $?: $(cat err.txt)"
    [ "$(grep -c "cores: '1025' is above the advised 1,024" err.txt)" = 1 ] || fail "cores=1025: no one warning: $(cat err.txt)"
    stored cores 1025
    pass 'cores=1025 writes with one warning'
    ;;
kinds)
    hs update retried=yes -t n:1 -f 2> err.txt || fail "retried=yes exited $?: $(cat err.txt)"
    stored retried True
    pass 'retried=yes stores true'
    hs update source=0198F3A2-1B2C-7D3E-8F40-0123456789AB -t n:1 -f 2> err.txt || fail "source exited $?: $(cat err.txt)"
    stored source 0198f3a2-1b2c-7d3e-8f40-0123456789ab
    pass 'a source uuid in upper case is stored in lower case'
    hs update "$id1" zone lab 2> err.txt || fail "ID zone lab exited $?: $(cat err.txt)"
    stored zone lab
    pass 'hs update ID zone lab: hs list shows lab'
    hs update zone=default -t n:1 -f 2> err.txt || fail "zone=default exited $?: $(cat err.txt)"
    stored zone default
    [ "$(hs list -c -w 'zone == none' 2>/dev/null)" = 3 ] || fail 'zone=default did not clear the zone'
    pass 'zone=default puts the task back in the default zone'
    refused 'retried: expected' retried=2 -t n:1 -f
    refused 'args: expected' args=none -t n:1 -f
    refused 'submit_id: expected' "$id1" submit_id none
    refused 'next_id: expected' 'next_id={0198f3a2-1b2c-7d3e-8f40-0123456789ab}' -t n:1 -f
    ;;
datetime)
    hs update start_time=2026-01-01T09:00 -t n:0 -f 2> err.txt || fail "naive start_time exited $?: $(cat err.txt)"
    hs update start_time=2026-01-01T14:00Z -t n:1 -f 2> err.txt || fail "UTC start_time exited $?: $(cat err.txt)"
    hs update "$(field id n:2)" start_time 2026-01-01T19:00+05:00 2> err.txt || fail "offset start_time exited $?: $(cat err.txt)"
    first=$(field start_time n:0)
    [ "$(field start_time n:1)" = "$first" ] && [ "$(field start_time n:2)" = "$first" ] ||
        fail "the three start times differ: $first, $(field start_time n:1), $(field start_time n:2)"
    case $backend in
        sqlite) [ "$first" = '2026-01-01 09:00:00' ] || fail "start_time shown as $first" ;;
        postgres) [ "$first" = '2026-01-01 14:00:00+00:00' ] || fail "start_time shown as $first" ;;
    esac
    pass "09:00 local, 14:00Z and 19:00+05:00 are one instant ($first)"
    refused 'start_time: expected' start_time=now -t n:1 -f
    refused 'submit_time: expected' "$id1" submit_time none
    hs update completion_time=2026-01-01 -t n:1 -f 2> err.txt || fail "completion_time exited $?: $(cat err.txt)"
    hs update completion_time=none -t n:1 -f 2> err.txt || fail "completion_time=none exited $?: $(cat err.txt)"
    stored completion_time null
    pass 'completion_time=none clears it'
    ;;
tags)
    refused 'KEY:VALUE' tag=x -t n:1 -f
    hs update color=blue -t n:1 -f 2> err.txt || fail "color=blue exited $?: $(cat err.txt)"
    [ "$(hs list -c -t color:blue 2>/dev/null)" = 1 ] || fail 'color=blue did not set the tag color'
    pass 'color=blue sets the tag color'
    refused 'did you mean exit_status?' exit_stauts=0 -t n:1 -f
    grep -q 'write exit_stauts:VALUE' err.txt || fail "no way out named: $(cat err.txt)"
    refused 'did you mean cores?' "$id1" Cores 4
    grep -q "write $id1 tag Cores:VALUE" err.txt || fail "no single-task way out named: $(cat err.txt)"
    hs update exit_stauts:0 -t n:1 -f 2> err.txt || fail "exit_stauts:0 exited $?: $(cat err.txt)"
    [ "$(hs list -c -t exit_stauts:0 2>/dev/null)" = 1 ] || fail 'exit_stauts:0 did not set a tag'
    stored exit_status null
    pass 'exit_stauts:0 sets a tag and leaves exit_status'
    refused 'Tag key must only contain' 'a b=1' -t n:1 -f
    printf 'no\n' | hs update exit_status=0 exti_stauts=0 color:red -t n:1 > out.txt 2> err.txt ||
        fail "prompt exited $?: $(cat err.txt)"
    grep -Eq '^ *column +exit_status=0$' out.txt && grep -Eq '^ *tag +exti_stauts:0$' out.txt &&
        grep -Eq '^ *tag +color:red$' out.txt || fail "prompt does not list the changes: $(cat out.txt)"
    stored exit_status null
    pass 'the prompt lists column and tag changes; no writes nothing'
    ;;
*)
    fail "unknown mode (numbers, kinds, datetime or tags)"
    ;;
esac
