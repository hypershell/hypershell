#!/bin/sh
# Run 02_core_probe.py against a throwaway database $1 under provider $2
# (postgres|timescale) on the local server, then drop it. Sandbox off.
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
uv run python "$here/02_core_probe.py"
admin "DROP DATABASE IF EXISTS $db WITH (FORCE)"
echo "dropped $db"
