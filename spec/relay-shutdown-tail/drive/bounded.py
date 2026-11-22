#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Run a command with a wall-clock bound, as `timeout` does where the host has none.

Usage: bounded.py SECONDS COMMAND [ARGS...]

The command runs in its own process group. Past SECONDS the whole group gets SIGINT, then SIGTERM
and SIGKILL if anything is left after each grace period, and this exits 124 (as GNU timeout does). Otherwise it exits
with the command's own status (128+N for a command killed by signal N).
"""

import os
import sys
import signal
import subprocess


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    bound = float(sys.argv[1])
    child = subprocess.Popen(sys.argv[2:], start_new_session=True)
    try:
        status = child.wait(timeout=bound)
    except subprocess.TimeoutExpired:
        print(f'bounded.py: {sys.argv[2]} still running after {bound:g} s - stopping its process group',
              file=sys.stderr)
        # SIGINT first: pytest runs its teardowns and a drive its `finally` (which stops what it started).
        for sig, grace in ((signal.SIGINT, 15), (signal.SIGTERM, 10), (signal.SIGKILL, 5)):
            try:
                os.killpg(child.pid, sig)
            except ProcessLookupError:
                break
            try:
                child.wait(timeout=grace)
                break
            except subprocess.TimeoutExpired:
                continue
        return 124
    except KeyboardInterrupt:
        os.killpg(child.pid, signal.SIGTERM)
        child.wait()
        return 130
    return status if status >= 0 else 128 - status


if __name__ == '__main__':
    sys.exit(main())
