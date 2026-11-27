# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""One task of the relay drive's workload: record the execution, wait, and maybe fail once.

    python -I -S relay_task.py SECONDS [fail]

Appends "<site> <task id>" to the node-local file named by HSD_EXEC_LOG, in one write (atomic on a
local filesystem), then sleeps. With `fail`, exits 3 on the task's first attempt (TASK_ATTEMPT) and
succeeds on any retry. A Python process, so that its environment can be read on every platform.
Standard library only; run with -I -S so nothing outside the standard library is imported.
"""

import os
import sys
import time

seconds = float(sys.argv[1])
fail = len(sys.argv) > 2 and sys.argv[2] == 'fail'
path = os.environ.get('HSD_EXEC_LOG')
if path:
    line = f"{os.environ.get('HSD_SITE', '?')} {os.environ.get('TASK_ID', '?')}\n".encode()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, line)
    finally:
        os.close(fd)
time.sleep(seconds)
if fail and os.environ.get('TASK_ATTEMPT', '1') == '1':
    sys.exit(3)
