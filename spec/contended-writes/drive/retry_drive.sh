#!/bin/sh
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0
#
# R1/R2 laptop drive: an `hsx` run with retries where every task fails, then a check that every
# failed row was retried exactly once. Run it from the repository root:
#
#   sh spec/contended-writes/drive/retry_drive.sh BACKEND [TASKS]
#
# BACKEND is `sqlite` (a fresh site database) or `timescale` (a fresh database under the `timescale`
# provider, `task` a hypertable, created and dropped on the server named by HS_TEST_POSTGRES).
# `hsx -r 1` runs TASKS (default 40) failing tasks on 4 executors, eager and not. The check reads
# the database: every attempt-1 row is failed, marked `retried` with a `next_id`, and has exactly
# one retry row naming it as `previous_id`, which is that `next_id`; no row is retried twice. The
# exit status is 0 only when both runs pass (hsx exit 0, no traceback). Every process started is waited on.

set -u

backend=${1:?usage: retry_drive.sh sqlite|timescale [TASKS]}
tasks=${2:-40}
root=$(pwd)

check() {
    uv run --quiet --extra postgres python -c '
import sys
from sqlalchemy import text
from hypershell.data.core import engine
with engine.connect() as conn:
    rows = conn.execute(text("SELECT id, attempt, retried, previous_id, next_id, exit_status FROM task")).all()
first = [r for r in rows if r.attempt == 1]
retries = {}
for r in rows:
    if r.previous_id is not None:
        retries.setdefault(str(r.previous_id), []).append(r)
problems = []
for r in first:
    chain = retries.get(str(r.id), [])
    if r.exit_status in (None, 0) or not r.retried or r.next_id is None:
        problems.append(f"task {r.id}: exit {r.exit_status}, retried {r.retried}, next {r.next_id}")
    elif len(chain) != 1 or str(chain[0].id) != str(r.next_id):
        problems.append(f"task {r.id}: {len(chain)} retry rows")
expected = int(sys.argv[1])
print(f"  {len(first)} first attempts, {sum(len(c) for c in retries.values())} retry rows, {len(problems)} problems")
for line in problems[:5]:
    print("  " + line)
sys.exit(0 if len(first) == expected and len(rows) == 2 * expected and not problems else 1)
' "$tasks"
}

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

status=0
for mode in plain eager; do
    site=$(mktemp -d "${TMPDIR:-/tmp}/hs-cw-retry.XXXXXX") || exit 2
    HYPERSHELL_SITE=$site
    HYPERSHELL_CONFIG_FILE=
    UV_PROJECT=$root
    export HYPERSHELL_SITE HYPERSHELL_CONFIG_FILE UV_PROJECT
    dbname=
    case $backend in
        sqlite)
            HYPERSHELL_DATABASE_FILE=$site/task.db; export HYPERSHELL_DATABASE_FILE ;;
        timescale)
            if [ -z "${HS_TEST_POSTGRES:-}" ]; then
                echo "retry_drive: timescale needs HS_TEST_POSTGRES" >&2; exit 2
            fi
            dbname="hs_cw_retry_$$_$mode"
            pg_admin create "$dbname" || exit 2
            pg_env
            HYPERSHELL_DATABASE_PROVIDER=timescale
            HYPERSHELL_DATABASE_DATABASE=$dbname
            export HYPERSHELL_DATABASE_PROVIDER HYPERSHELL_DATABASE_DATABASE ;;
        *) echo "retry_drive: unknown backend $backend" >&2; exit 2 ;;
    esac
    (cd "$site" && uv run --quiet --extra postgres hs initdb --yes >"$site/initdb.log" 2>&1) || {
        echo "retry_drive: hs initdb failed (see $site/initdb.log)"; exit 2; }
    seq "$tasks" > "$site/tasks.in"
    flag=
    [ "$mode" = eager ] && flag=--eager
    (cd "$site" && uv run --quiet --extra postgres hsx tasks.in -t 'exit 3' -r 1 $flag -N4 \
        >"$site/hsx.out" 2>"$site/hsx.err") &
    pid=$!
    wait "$pid"
    rc=$?
    echo "retry_drive: $backend, $mode: hsx exit $rc"
    (cd "$site" && check) || status=1
    if [ "$rc" -ne 0 ] || grep -q -i 'traceback\|StaleDataError' "$site/hsx.err"; then
        echo "  hsx exited $rc; stderr: $(grep -m 1 -i 'error' "$site/hsx.err")"
        status=1
    fi
    [ -n "$dbname" ] && pg_admin drop "$dbname"
    find "$site" -type f -delete 2>/dev/null
    find "$site" -depth -type d -empty -delete 2>/dev/null
done
exit "$status"
