#!/bin/sh
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0
#
# Generate a SQLite database with hypershell==2.8.3's own code paths.
#
# Usage:
#   make-2.8.3-db.sh <python-with-hypershell-2.8.3> <out-dir>
#
# Produces <out-dir>/hypershell-2.8.3.db holding three completed tasks, one failure retried
# once (a two-row chain), two still-pending tasks, and one client row.
#
# The run is isolated from the caller's HyperShell configuration, binds a free local port,
# and pins TZ=UTC and socket.gethostname so that no machine-specific value lands in the
# file. The script refuses to keep the file if the local hostname, username, or output
# directory appears in it.
set -eu

if [ "$#" -ne 2 ]; then
    echo "usage: $0 <python-with-hypershell-2.8.3> <out-dir>" >&2
    exit 2
fi

PY="$1"
OUT="$2"
DB_NAME="hypershell-2.8.3.db"

mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"
DB="$OUT/$DB_NAME"
if [ -e "$DB" ]; then
    echo "error: $DB already exists" >&2
    exit 2
fi
cd "$OUT"

# Drop every inherited HYPERSHELL_* variable, then pin a clean baseline.
for name in $(env | sed -n 's/^\(HYPERSHELL_[A-Za-z0-9_]*\)=.*/\1/p'); do
    unset "$name"
done
HYPERSHELL_CONFIG_FILE=''
HYPERSHELL_SITE="$OUT"
HYPERSHELL_DATABASE_FILE="$DB"
HYPERSHELL_SERVER_PORT="$("$PY" -c 'import socket; s = socket.socket(); s.bind(("localhost", 0)); print(s.getsockname()[1])')"
TZ=UTC
export TZ HYPERSHELL_CONFIG_FILE HYPERSHELL_SITE HYPERSHELL_DATABASE_FILE HYPERSHELL_SERVER_PORT

"$PY" -c 'import hypershell; assert hypershell.__version__ == "2.8.3", hypershell.__version__'

# Run a hypershell entry point ($1 = main | main_x) with the hostname pinned.
# Local `hsx` runs its client in-process, so the patch covers every host column.
hs283() {
    entry="$1"
    shift
    "$PY" -c 'import socket, sys
socket.gethostname = lambda: "fixture-host"
import hypershell
sys.argv[0] = "hs"
sys.exit(getattr(hypershell, sys.argv.pop(1))())' "$entry" "$@"
}

cat > run.in <<'IN'
echo done-0  # HYPERSHELL: kind:done n:0
echo done-1  # HYPERSHELL: kind:done n:1
echo done-2  # HYPERSHELL: kind:done n:2
sh -c 'exit 3'  # HYPERSHELL: kind:fail
IN

# Three completed tasks and one failure retried once (attempt 1 retried -> attempt 2 failed).
hs283 main_x run.in -N2 -b1 -w1 --max-retries 1 --no-tls -o out.txt -e err.txt

# Two still-pending tasks, submitted after the run.
hs283 main submit -t kind:pending n:0 -- echo pending-0
hs283 main submit -t kind:pending n:1 -- echo pending-1

# Refuse to keep a file that carries an identifier from this machine.
leaked=''
for needle in "$(hostname)" "$(hostname -s 2>/dev/null || true)" "$(id -un)" "$OUT"; do
    if [ "${#needle}" -ge 3 ] && grep -a -q -F -- "$needle" "$DB"; then
        leaked="$leaked '$needle'"
    fi
done
if [ -n "$leaked" ]; then
    mv "$DB" "$DB.rejected"
    echo "error: refusing to keep $DB_NAME; found:$leaked (moved to $DB.rejected)" >&2
    exit 1
fi

echo "$DB"
