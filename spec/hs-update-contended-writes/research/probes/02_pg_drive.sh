#!/bin/sh
# Q2 driver for a server database: creates database $1 on the local server,
# runs 02_multikey.sh under provider $2 (postgres|timescale), then drops it.
# Needs the sandbox off (local TCP). Run from the worktree root.
set -u
db="$1"; provider="$2"; here="$(cd "$(dirname "$0")" && pwd)"
site="$(mktemp -d "${TMPDIR:-/tmp}/hu02-site.XXXXXX")"
url=postgresql+psycopg://postgres:postgres@localhost:5432/postgres
admin() { uv run python -c "
import sys, sqlalchemy as sa
e = sa.create_engine('$url', isolation_level='AUTOCOMMIT')
with e.connect() as c:
    c.execute(sa.text(sys.argv[1]))
" "$1"; }
admin "DROP DATABASE IF EXISTS $db WITH (FORCE)"
admin "CREATE DATABASE $db"
unset HYPERSHELL_DATABASE_FILE
export HYPERSHELL_SITE="$site" HYPERSHELL_DATABASE_PROVIDER="$provider" \
  HYPERSHELL_DATABASE_HOST=localhost HYPERSHELL_DATABASE_PORT=5432 \
  HYPERSHELL_DATABASE_USER=postgres HYPERSHELL_DATABASE_PASSWORD=postgres \
  HYPERSHELL_DATABASE_DATABASE="$db"
uv run hs initdb --yes 2>&1 | tail -2
sh "$here/02_multikey.sh"
uv run python -c "
import sqlalchemy as sa
e = sa.create_engine('${url%/postgres}/$db')
with e.connect() as c:
    print('server:', c.execute(sa.text('select version()')).scalar()[:16],
          '| timescaledb:', c.execute(sa.text(\"select extversion from pg_extension where extname='timescaledb'\")).scalar(),
          '| hypertable:', c.execute(sa.text(\"select count(*) from pg_class where relname='task'\")).scalar() and
          c.execute(sa.text(\"select to_regclass('timescaledb_information.hypertables') is not null\")).scalar() and
          c.execute(sa.text(\"select count(*) from timescaledb_information.hypertables where hypertable_name='task'\")).scalar())
" 2>&1 | tail -1
admin "DROP DATABASE IF EXISTS $db WITH (FORCE)"
echo "dropped $db"
