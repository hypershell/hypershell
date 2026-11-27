# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Test initdb operations."""


# Type annotations
from __future__ import annotations

# Standard libs
import sqlite3
from pathlib import Path

# External libs
from pytest import mark
from cmdkit.app import exit_status

# Internal libs
from tests import main, main_lines, NO_OUTPUT, create_taskfile


@mark.integration
def test_which_database(temp_site: Path) -> None:
    """Confirm ephemeral database configuration."""
    assert main_lines(['hs', 'config', 'which', 'database.file']) == (
        exit_status.success,
        [f'{temp_site}/local.db (env: HYPERSHELL_DATABASE_FILE | default: null)', ],
        NO_OUTPUT
    )


@mark.integration
def test_list_empty_list(temp_site: Path) -> None:
    """New database is empty."""
    assert main_lines(['hs', 'list', '--limit', '1']) == (
        exit_status.success, NO_OUTPUT, NO_OUTPUT
    )


@mark.integration
def test_list_empty_count(temp_site: Path) -> None:
    """New database is empty."""
    assert main_lines(['hs', 'list', '--count']) == (
        exit_status.success, ['0', ], NO_OUTPUT
    )


@mark.integration
def test_vacuum(temp_site: Path) -> None:
    """Vacuum database with prompt."""
    assert main_lines(['hs', 'initdb', '--yes']) == (
        exit_status.success, NO_OUTPUT, [
        f'INFO [hypershell.data] SQLite database initialized automatically',
        f'INFO [hypershell.data] Optimizing database {temp_site / "local.db"}'
    ])
    assert main_lines(['hs', 'initdb', '--vacuum']) == (
        exit_status.runtime_error, NO_OUTPUT,
        ['CRITICAL [hypershell.data] RuntimeError: Non-interactive prompt cannot confirm (see --yes).', ]
    )
    assert main_lines(['hs', 'initdb', '--vacuum', '--yes']) == (
        exit_status.success, NO_OUTPUT, [
        f'INFO [hypershell.data] Vacuuming database {temp_site / "local.db"}',
        f'INFO [hypershell.data] Cleaned 0.00B from {temp_site / "local.db"}'
    ])


def _task_parts(path: Path) -> list[tuple[int, object]]:
    """Read each task's `part` column and its `part` tag-JSON key from a SQLite file."""
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute("select part, json_extract(tag, '$.part') from task").fetchall()
    finally:
        conn.close()


@mark.integration
def test_rotate(temp_site: Path) -> None:
    """Rotation moves completed tasks into the next partition via the `part` column.

    Completed tasks (exit_status set) are stamped with the new partition's index in the
    `part` **column** — never the tag — cloned into the partition file, and dropped from
    main; incomplete tasks stay behind at part 0. Auto-union re-joins both files at read
    time; `--ignore-partitions` sees only main.
    """
    main_db = temp_site / 'local.db'
    partition = temp_site / 'local.1'

    # Four tasks; complete two (n:0, n:1), leave two (n:2, n:3) unscheduled.
    taskfile = create_taskfile(temp_site, [f'echo {n}  # HYPERSHELL: n:{n}' for n in range(4)])
    assert main(['hs', 'submit', str(taskfile)])[0] == exit_status.success
    assert main(['hs', 'update', 'exit_status=0', '-t', 'n:0', '--no-confirm'])[0] == exit_status.success
    assert main(['hs', 'update', 'exit_status=0', '-t', 'n:1', '--no-confirm'])[0] == exit_status.success

    # Rotate: completed -> local.1 at part 1, main keeps the two incomplete at part 0.
    assert main(['hs', 'initdb', '--rotate', '--yes'])[0] == exit_status.success
    assert partition.exists()

    main_rows = _task_parts(main_db)
    part_rows = _task_parts(partition)
    assert sorted(part for part, _ in main_rows) == [0, 0]       # incomplete tasks stay at part 0
    assert sorted(part for part, _ in part_rows) == [1, 1]       # completed tasks carry the partition index
    assert all(tag_part is None for _, tag_part in main_rows + part_rows)   # `part` never leaks into tag JSON

    # Auto-union re-joins both files; --ignore-partitions restricts to main.
    assert main_lines(['hs', 'list', '--count'])[1] == ['4']
    assert main_lines(['hs', 'list', '--count', '--ignore-partitions'])[1] == ['2']
