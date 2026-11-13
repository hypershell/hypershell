# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Read-only sampler of the upstream task table for the relay-retire drive.

    python retire_sampler.py DB OUT [INTERVAL]

Every INTERVAL seconds (default 0.25) it reads, from the upstream server's SQLite database DB opened
read-only, every incomplete task with the client it is assigned to, and appends one JSON line to OUT:

    {"t": EPOCH, "rows": [[TASK_ID, CLIENT_ID or null], ...]}

A read that fails (the server holding a write lock past the timeout) is skipped. It runs until it is
sent SIGTERM or SIGINT. Standard library only, so it runs with any interpreter.
"""

import sys
import json
import time
import signal
import sqlite3


def sample(db: str) -> list:
    """Every incomplete task and its client id, read once."""
    conn = sqlite3.connect(f'file:{db}?mode=ro', uri=True, timeout=1)
    try:
        return [list(row) for row in conn.execute('SELECT id, client_id FROM task WHERE completion_time IS NULL '
                                                  'ORDER BY id').fetchall()]
    finally:
        conn.close()


def main() -> int:
    """Sample until signalled."""
    db, out = sys.argv[1], sys.argv[2]
    interval = float(sys.argv[3]) if len(sys.argv) > 3 else 0.25
    stopping = []
    signal.signal(signal.SIGTERM, lambda *_: stopping.append(1))
    signal.signal(signal.SIGINT, lambda *_: stopping.append(1))
    with open(out, mode='a') as stream:
        while not stopping:
            began = time.time()
            try:
                rows = sample(db)
            except sqlite3.Error:
                rows = None
            if rows is not None:
                stream.write(json.dumps({'t': round(began, 3), 'rows': rows}) + '\n')
                stream.flush()
            time.sleep(max(0.0, interval - (time.time() - began)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
