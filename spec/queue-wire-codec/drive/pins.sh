#!/bin/sh
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0
#
# Runs the CPython-internals pins (tests/test_queue.py::TestPrivateInternals, which includes the queue
# server's own start-up check of every pinned internal) on each Python the project supports, each in
# its own isolated environment. Run it from anywhere in the repository:
#
#   sh spec/queue-wire-codec/drive/pins.sh
#
# Each interpreter comes through uv (`uv run --isolated --python 3.X`), which needs pypi.org and
# files.pythonhosted.org the first time. Each run is bounded (PINS_BOUND seconds, default 600). One
# line is printed per Python; the exit status is 0 only when the pins pass on every one.

set -u

here=$(cd "$(dirname "$0")" && pwd)
root=$(git -C "$here" rev-parse --show-toplevel) || exit 2
bounded=$root/spec/submit-input-checks/drive/bounded.py
bound=${PINS_BOUND:-600}
cd "$root" || exit 2

status=0
for version in 3.11 3.12 3.13 3.14; do
    out=$(python3 "$bounded" "$bound" env -u UV_PROJECT_ENVIRONMENT uv run --quiet --isolated --python "$version" \
        pytest -q -p no:cacheprovider tests/test_queue.py::TestPrivateInternals 2>&1)
    code=$?
    summary=$(printf '%s\n' "$out" | grep -E '(passed|failed|error)' | tail -n 1)
    if [ "$code" -eq 0 ]; then
        echo "pins: Python $version ok ($summary)"
    else
        echo "pins: Python $version FAILED (exit $code: $summary)"
        printf '%s\n' "$out" | tail -n 40
        status=1
    fi
done
exit $status
