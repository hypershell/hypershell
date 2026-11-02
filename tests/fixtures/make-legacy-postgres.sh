#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0
#
# Build PostgreSQL template databases with released hypershell code, for the upgrade tests.
#
#   hs283    created and populated by hypershell[postgres]==2.8.3: three completed tasks, one
#            failure retried once (a two-row chain), and two still-pending tasks.
#   hs290a1  a copy of hs283 opened by hypershell[postgres]==2.9.0a1 (`hs initdb --yes`), which
#            creates the new `source` table but leaves `task` without its new columns.
#
# Usage:
#   make-legacy-postgres.sh
#
# Environment:
#   PG_CONTAINER   Docker container running the server; admin SQL runs through `docker exec psql`.
#   PGHOST         Server address seen from this host (default: localhost).
#   PGPORT         Server port (default: 5432).
#   PGUSER         Admin role (default: postgres).
#   PGPASSWORD     Admin password (default: postgres).
#   WORKDIR        Scratch directory for the release environments (default: $RUNNER_TEMP or $TMPDIR).
#
# Every process exits before the script does, so the templates have no open connections.
set -euo pipefail

: "${PG_CONTAINER:?set PG_CONTAINER to the PostgreSQL container id}"
PGHOST="${PGHOST:-localhost}"
PGPORT="${PGPORT:-5432}"
PGUSER="${PGUSER:-postgres}"
PGPASSWORD="${PGPASSWORD:-postgres}"
WORKDIR="${WORKDIR:-${RUNNER_TEMP:-${TMPDIR:-/tmp}}}/hypershell-legacy-postgres"
mkdir -p "$WORKDIR"

admin_sql() {
    docker exec "$PG_CONTAINER" psql -v ON_ERROR_STOP=1 -X -q -t -A -U "$PGUSER" -d "${2:-postgres}" -c "$1"
}

make_env() {  # $1 = directory, $2 = requirement
    uv venv --quiet --python 3.12 "$1"
    uv pip install --quiet --python "$1/bin/python" "$2"
}

# Run a release's CLI against database $1 with an isolated configuration; rest = command.
release() {
    local database="$1"
    shift
    env -i PATH="$PATH" HOME="$WORKDIR" TZ=UTC \
        HYPERSHELL_CONFIG_FILE='' \
        HYPERSHELL_SITE="$WORKDIR/site-$database" \
        HYPERSHELL_SERVER_PORT="$(python3 -c 'import socket; s = socket.socket(); s.bind(("localhost", 0)); print(s.getsockname()[1])')" \
        HYPERSHELL_DATABASE_PROVIDER=postgres \
        HYPERSHELL_DATABASE_HOST="$PGHOST" \
        HYPERSHELL_DATABASE_PORT="$PGPORT" \
        HYPERSHELL_DATABASE_USER="$PGUSER" \
        HYPERSHELL_DATABASE_PASSWORD="$PGPASSWORD" \
        HYPERSHELL_DATABASE_DATABASE="$database" \
        "$@"
}

env283="$WORKDIR/env-2.8.3"
env290="$WORKDIR/env-2.9.0a1"
make_env "$env283" 'hypershell[postgres]==2.8.3'
make_env "$env290" 'hypershell[postgres]==2.9.0a1'
"$env283/bin/python" -c 'import hypershell; assert hypershell.__version__ == "2.8.3", hypershell.__version__'
"$env290/bin/python" -c 'import hypershell; assert hypershell.__version__ == "2.9.0a1", hypershell.__version__'

admin_sql 'DROP DATABASE IF EXISTS hs290a1'
admin_sql 'DROP DATABASE IF EXISTS hs283'
admin_sql 'CREATE DATABASE hs283'

mkdir -p "$WORKDIR/site-hs283"
cat > "$WORKDIR/site-hs283/run.in" <<'IN'
echo done-0  # HYPERSHELL: kind:done n:0
echo done-1  # HYPERSHELL: kind:done n:1
echo done-2  # HYPERSHELL: kind:done n:2
sh -c 'exit 3'  # HYPERSHELL: kind:fail
IN

(
    cd "$WORKDIR/site-hs283"
    release hs283 "$env283/bin/hs" initdb --yes
    # Three completed tasks and one failure retried once (attempt 1 retried -> attempt 2 failed).
    release hs283 "$env283/bin/hsx" run.in -N2 -b1 -w1 --max-retries 1 --no-tls -o out.txt -e err.txt
    # Two still-pending tasks, submitted after the run.
    release hs283 "$env283/bin/hs" submit -t kind:pending n:0 -- echo pending-0
    release hs283 "$env283/bin/hs" submit -t kind:pending n:1 -- echo pending-1
)

count="$(admin_sql 'SELECT count(*) FROM task' hs283)"
test "$count" = 7 || { echo "error: hs283 holds $count tasks, expected 7" >&2; exit 1; }
chain="$(admin_sql "SELECT count(*) FROM task WHERE previous_id IS NOT NULL OR next_id IS NOT NULL" hs283)"
test "$chain" = 2 || { echo "error: hs283 holds $chain chained tasks, expected 2" >&2; exit 1; }

admin_sql 'CREATE DATABASE hs290a1 TEMPLATE hs283'
mkdir -p "$WORKDIR/site-hs290a1"
(
    cd "$WORKDIR/site-hs290a1"
    release hs290a1 "$env290/bin/hs" initdb --yes
)

source_table="$(admin_sql "SELECT count(*) FROM information_schema.tables WHERE table_name = 'source'" hs290a1)"
source_column="$(admin_sql "SELECT count(*) FROM information_schema.columns WHERE table_name = 'task' AND column_name = 'source'" hs290a1)"
test "$source_table" = 1 && test "$source_column" = 0 || {
    echo "error: hs290a1 is not half-upgraded (source table: $source_table, task.source: $source_column)" >&2
    exit 1
}

echo "Built template databases: hs283 (7 tasks), hs290a1 (half-upgraded)"
