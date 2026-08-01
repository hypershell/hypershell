# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""End-to-end integration test for the `card` output format.

Runs a real (file-mode) cluster so tasks reach a genuine terminal state, then drives
`hs list`, `hs info`, and `hs wait --info` with `--format=card` through the installed CLI.
"""


# Type annotations
from __future__ import annotations

# Standard libs
import re
from pathlib import Path

# External libs
from pytest import mark
from cmdkit.app import exit_status

# Internal libs
from tests import main, main_lines, create_taskfile


ANSI = re.compile(r'\x1b\[[0-9;]*m')
UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')


def strip_ansi(text: str) -> str:
    return ANSI.sub('', text)


SOURCE_ID_FP = re.compile(r'source id [0-9a-f-]{36} \([0-9a-f]{32}\)')


@mark.integration
def test_card_format_end_to_end(temp_site: Path, monkeypatch) -> None:
    """`--format=card` renders completed tasks as OK cards via list, info, and wait."""
    # A wide, non-folding card keeps the `source id <uuid> (<fingerprint>)` line intact to assert on.
    monkeypatch.setenv('COLUMNS', '160')
    taskfile = create_taskfile(temp_site, ['echo CARD_0  # HYPERSHELL: n:0',
                                           'echo CARD_1  # HYPERSHELL: n:1'])
    rc, _, _ = main(['hs', 'cluster', str(taskfile), '-N1'])
    assert rc == exit_status.success

    # hs list --format=card → one OK card per completed task, each carrying the batched
    # Source content-fingerprint in parens on the source id line.
    rc, out, _ = main(['hs', 'list', '--format=card'])
    assert rc == exit_status.success
    listing = strip_ansi(out)
    assert listing.count('╭─ task') == 2
    assert listing.count('status: OK') == 2
    assert len(SOURCE_ID_FP.findall(listing)) == 2

    # hs info <id> --format=card → a single OK card carrying the full id.
    rc, ids, _ = main_lines(['hs', 'list', 'id'])
    assert rc == exit_status.success
    task_ids = [line for line in ids if UUID.fullmatch(line)]
    assert len(task_ids) == 2
    task_id = task_ids[0]
    rc, out, _ = main(['hs', 'info', task_id, '--format=card'])
    assert rc == exit_status.success
    info = strip_ansi(out)
    assert info.count('╭─ task') == 1
    assert task_id in info
    assert 'status: OK' in info
    # The single-task path resolves the same fingerprint directly via `Source.from_id`.
    assert SOURCE_ID_FP.search(info)

    # hs wait --info -f card → completed task returns immediately with the same card.
    rc, out, _ = main(['hs', 'wait', task_id, '--info', '-f', 'card'])
    assert rc == exit_status.success
    assert 'status: OK' in strip_ansi(out)
