#!/bin/sh
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0
#
# R4 laptop drive: concurrent first `hs submit`s from stdin against a database whose `source` table
# is empty. Run it from the repository root:
#
#   sh spec/contended-writes/drive/concurrent_submit.sh BACKEND [ROUNDS] [PROCS]
#
# BACKEND is one of:
#   sqlite      a fresh SQLite database (`hs initdb --yes` first)
#   sqlite-283  tests/fixtures/hypershell-2.8.3.db, upgraded in place by `hs initdb --yes` first
#   postgres    a fresh PostgreSQL database under the `postgres` provider, created and dropped on
#               the server named by HS_TEST_POSTGRES (an admin URL, as for the test suite)
#
# Each round gets its own site under $TMPDIR, then starts PROCS (default 6) `hs submit -f -` at once,
# each reading 5 tasks from stdin. A round fails when any submit exits non-zero or the database does
# not end with every task (those it held before, plus PROCS x 5). One line is printed per failed round, then a summary; the exit status is
# 0 only when no round failed. Every process started is waited on by its recorded PID.

set -u

backend=${1:?usage: concurrent_submit.sh sqlite|sqlite-283|postgres [ROUNDS] [PROCS]}
rounds=${2:-10}
procs=${3:-6}
per=5
root=$(pwd)
fixture="$root/tests/fixtures/hypershell-2.8.3.db"

case $backend in
    sqlite|sqlite-283) ;;
    postgres)
        if [ -z "${HS_TEST_POSTGRES:-}" ]; then
            echo "concurrent_submit: postgres needs HS_TEST_POSTGRES" >&2
            exit 2
        fi
        ;;
    *) echo "concurrent_submit: unknown backend $backend" >&2; exit 2 ;;
esac

# Create or drop one database on the admin server (no psql needed).
pg_admin() {
    uv run --quiet --extra postgres python -c '
import os, sys
from sqlalchemy import create_engine, text
engine = create_engine(os.environ["HS_TEST_POSTGRES"], isolation_level="AUTOCOMMIT")
with engine.connect() as conn:
    conn.execute(text(f"DROP DATABASE IF EXISTS {sys.argv[2]} WITH (FORCE)"))
    if sys.argv[1] == "create":
        conn.execute(text(f"CREATE DATABASE {sys.argv[2]}"))
engine.dispose()
' "$1" "$2"
}

pg_env() {
    eval "$(uv run --quiet python -c '
import os, shlex
from sqlalchemy.engine import make_url
url = make_url(os.environ["HS_TEST_POSTGRES"])
for name, value in (("HOST", url.host or "localhost"), ("PORT", url.port or 5432),
                    ("USER", url.username or "postgres"), ("PASSWORD", url.password or "")):
    print(f"HYPERSHELL_DATABASE_{name}={shlex.quote(str(value))}; export HYPERSHELL_DATABASE_{name}")
')"
}

failed=0
round=1
while [ "$round" -le "$rounds" ]; do
    site=$(mktemp -d "${TMPDIR:-/tmp}/hs-cw-submit.XXXXXX") || exit 2
    HYPERSHELL_SITE=$site
    HYPERSHELL_CONFIG_FILE=
    UV_PROJECT=$root
    export HYPERSHELL_SITE HYPERSHELL_CONFIG_FILE UV_PROJECT
    unset HYPERSHELL_DATABASE_PROVIDER HYPERSHELL_DATABASE_DATABASE HYPERSHELL_DATABASE_FILE 2>/dev/null
    dbname=
    case $backend in
        sqlite)
            HYPERSHELL_DATABASE_FILE=$site/task.db; export HYPERSHELL_DATABASE_FILE ;;
        sqlite-283)
            cp "$fixture" "$site/task.db"
            HYPERSHELL_DATABASE_FILE=$site/task.db; export HYPERSHELL_DATABASE_FILE ;;
        postgres)
            dbname="hs_cw_submit_$$_$round"
            pg_admin create "$dbname" || exit 2
            pg_env
            HYPERSHELL_DATABASE_PROVIDER=postgres
            HYPERSHELL_DATABASE_DATABASE=$dbname
            export HYPERSHELL_DATABASE_PROVIDER HYPERSHELL_DATABASE_DATABASE ;;
    esac
    (cd "$site" && uv run --quiet hs initdb --yes >"$site/initdb.log" 2>&1) || {
        echo "round $round: hs initdb failed (see $site/initdb.log)"; exit 2; }
    base=$(cd "$site" && uv run --quiet hs list --count 2>/dev/null)

    pids=
    n=1
    while [ "$n" -le "$procs" ]; do
        (cd "$site" && seq "$per" | sed "s/^/echo $n-/" | uv run --quiet hs submit -f - \
            >"$site/submit-$n.out" 2>&1) &
        pids="$pids $!"
        n=$((n + 1))
    done
    bad=0
    for pid in $pids; do
        wait "$pid" || bad=$((bad + 1))
    done
    count=$(cd "$site" && uv run --quiet hs list --count 2>/dev/null)
    expected=$((base + procs * per))
    if [ "$bad" -ne 0 ] || [ "$count" != "$expected" ]; then
        failed=$((failed + 1))
        why=$(cat "$site"/submit-*.out | grep -m 1 -o 'IntegrityError[^)]*)\?' || true)
        echo "round $round: $bad submit(s) failed, $count of $expected tasks${why:+ - $why}"
    fi
    [ -n "$dbname" ] && pg_admin drop "$dbname"
    del_site=$site
    find "$del_site" -type f -delete 2>/dev/null
    find "$del_site" -depth -type d -empty -delete 2>/dev/null
    round=$((round + 1))
done

echo "concurrent_submit: $backend, $rounds rounds of $procs submits: $failed failed"
[ "$failed" -eq 0 ]
