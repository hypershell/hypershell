# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
Kill site clients the moment the server logs a plan: the race topology_drive.sh's stall modes need.

    python topology_trigger.py SERVER_LOG OFFSET PATTERN LAUNCH_LOG LABEL COUNT OUT DEADLINE

Follows SERVER_LOG from byte OFFSET. At the first line matching PATTERN (a regular expression), it
sends SIGKILL to the first COUNT site clients of launcher label LABEL still running (their pids from
the shim's LAUNCH_LOG, oldest first), writes `killed <epoch> <pid>...` to OUT and exits 0. A relay
reads a new plan only after its next heartbeat, so a client killed here leaves after the server
counted it and before the relay applies the plan made with it. Exits 1 if no line matches within
DEADLINE seconds. Kills nothing else; stdlib only.
"""

import os
import re
import sys
import time
import signal


def running(launch_log: str, label: str) -> list:
    """Pids of the site clients launched under `label` that are still running, oldest first."""
    pids = []
    with open(launch_log) as stream:
        for line in stream:
            fields = dict(item.split('=', 1) for item in line.split() if '=' in item)
            if fields.get('site') == label and fields.get('pid', '').isdigit():
                pid = int(fields['pid'])
                try:
                    os.kill(pid, 0)
                    pids.append(pid)
                except (ProcessLookupError, PermissionError):
                    pass
    return pids


def main() -> int:
    server_log, offset, pattern, launch_log, label, count, out, deadline = sys.argv[1:9]
    matcher = re.compile(pattern)
    stop = time.monotonic() + float(deadline)
    with open(server_log) as stream:
        stream.seek(int(offset))
        pending = ''
        while time.monotonic() < stop:
            chunk = stream.readline()
            if not chunk:
                time.sleep(0.02)
                continue
            pending += chunk
            if not pending.endswith('\n'):
                continue
            line, pending = pending, ''
            if matcher.search(line):
                killed = running(launch_log, label)[:int(count)]
                for pid in killed:
                    os.kill(pid, signal.SIGKILL)
                with open(out, 'w') as record:
                    record.write(f'killed {int(time.time())} {" ".join(map(str, killed))}\n')
                return 0
    return 1


if __name__ == '__main__':
    sys.exit(main())
