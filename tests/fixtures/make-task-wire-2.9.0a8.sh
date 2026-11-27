#!/bin/sh
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0
#
# Print the task wire as hypershell 2.9.0a8's own code encodes it, for tests/test_task_wire.py.
#
# Usage:
#   git archive 2.9.0a8 src | tar -x -C "$TMPDIR/hs-2.9.0a8"
#   make-task-wire-2.9.0a8.sh <python> "$TMPDIR/hs-2.9.0a8/src"
#
# <python> needs SQLAlchemy and cmdkit (the project's .venv/bin/python will do); the 2.9.0a8 package
# is imported from the extracted source directory, never from the environment. The site the import
# reads is a temporary directory that Python removes on exit.
#
# It encodes one fixed task (fixed ids, aware times with and without microseconds, one naive time,
# the date-like line 2026-11-02 and a date-like tag value) and prints three Python bytes literals:
#   DISPATCH  the text a server sends a client for the task, json.dumps(task.to_json())
#   BUNDLE    serialize_tasks([task])
#   RESULT    serialize_tasks([result]), the same task as a client returns it with command 2026-11-02
# Every value is a literal, so the output is the same on every machine and run.
set -eu

if [ "$#" -ne 2 ]; then
    echo "usage: $0 <python> <extracted-2.9.0a8-src-dir>" >&2
    exit 2
fi

PY="$1"
SRC="$(cd "$2" && pwd)"
[ -f "$SRC/hypershell/__init__.py" ] || { echo "error: no hypershell package under $SRC" >&2; exit 2; }

# Drop every inherited HYPERSHELL_* variable; the Python side pins a throwaway site.
for name in $(env | sed -n 's/^\(HYPERSHELL_[A-Za-z0-9_]*\)=.*/\1/p'); do
    unset "$name"
done
TZ=UTC
export TZ

exec "$PY" -I - "$SRC" <<'EOF'
import os
import sys
import tempfile

site = tempfile.TemporaryDirectory(prefix='task-wire-site.')
os.environ.update(HYPERSHELL_CONFIG_FILE='', HYPERSHELL_SITE=site.name,
                  HYPERSHELL_DATABASE_FILE=os.path.join(site.name, 'task.db'))
sys.path.insert(0, sys.argv[1])

import json
from datetime import datetime, timedelta, timezone

import hypershell
from hypershell.data.model import Task, serialize_tasks

assert hypershell.__version__ == '2.9.0a8', hypershell.__version__
assert hypershell.__file__.startswith(sys.argv[1]), hypershell.__file__

west = timezone(timedelta(hours=-5))
fields = {name: None for name in Task.columns}
fields.update({
    'id': '6f1c2a3e-8b4d-4e5f-9a0b-1c2d3e4f5a6b',
    'group': 0,
    'args': '2026-11-02',
    'submit_id': '0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d',
    'submit_time': datetime(2026, 11, 2, 9, 30, 0, 123456, tzinfo=west),
    'submit_host': 'submit-host',
    'cores': 1,
    'server_id': '1b2c3d4e-5f6a-4b7c-9d8e-0f1a2b3c4d5e',
    'server_host': 'server-host',
    'schedule_time': datetime(2026, 11, 2, 14, 31, 0, tzinfo=timezone.utc),
    'attempt': 1,
    'retried': False,
    'source': '2c3d4e5f-6a7b-4c8d-8e9f-1a2b3c4d5e6f',
    'fingerprint': '3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e',
    'part': 0,
    'tag': {'day': '2026-11-02'},
})
task = Task(**fields)
result = Task(**{**fields,
                 'client_id': '4e5f6a7b-8c9d-4e0f-a1b2-c3d4e5f6a7b8',
                 'client_host': 'client-host',
                 'command': '2026-11-02',
                 'start_time': datetime(2026, 11, 2, 9, 31, 1, 654321, tzinfo=west),
                 'completion_time': datetime(2026, 11, 2, 9, 31, 3, 5),
                 'waited': 61,
                 'duration': 2,
                 'exit_status': 0,
                 'outpath': '2026-11-02.out',
                 'errpath': '2026-11-02.err'})

print('DISPATCH =', repr(json.dumps(task.to_json()).encode('utf-8')))
print('BUNDLE =', repr(serialize_tasks([task])))
print('RESULT =', repr(serialize_tasks([result])))
EOF
