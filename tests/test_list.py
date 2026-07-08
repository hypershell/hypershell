# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Test task listing: exit-status colorization and the --signal filter."""


# Type annotations
from __future__ import annotations

# Standard libs
import os
import re
import shutil
import subprocess
from pathlib import Path

# External libs
from pytest import mark, fixture, skip
from cmdkit.app import exit_status as cli_status
from cmdkit.ansi import red, green, yellow, faint

# Internal libs
from hypershell.task import select_color, select_style, no_color, DATABASE_TOO_BIG
from tests import main, main_lines, create_taskfile


# SGR codes emitted by cmdkit.ansi / rich for each status color.
GREEN, RED, YELLOW, FAINT = '\x1b[32m', '\x1b[31m', '\x1b[33m', '\x1b[2m'
ANSI = re.compile(r'\x1b\[[0-9;]*m')


def strip_ansi(text: str) -> str:
    """Remove ANSI SGR sequences."""
    return ANSI.sub('', text)


@fixture
def three_colored_tasks(temp_site: Path, monkeypatch) -> Path:
    """Submit n:0 (success/green), n:1 (cancelled/faint), n:2 (failure/red); force color on."""
    taskfile = create_taskfile(temp_site, [f'echo {n}  # HYPERSHELL: n:{n}' for n in range(3)])
    assert main(['hs', 'submit', str(taskfile)])[0] == cli_status.success
    assert main(['hs', 'update', 'exit_status=0', '-t', 'n:0', '--no-confirm'])[0] == cli_status.success
    assert main(['hs', 'update', 'exit_status=-1', '-t', 'n:1', '--no-confirm'])[0] == cli_status.success
    assert main(['hs', 'update', 'exit_status=1', '-t', 'n:2', '--no-confirm'])[0] == cli_status.success
    monkeypatch.setenv('FORCE_COLOR', '1')  # read by cmdkit.ansi / rich in the child process
    return temp_site


@mark.unit
class TestSelectColor:
    """Unit tests for exit-status based colorization."""

    def test_color_scheme(self) -> None:
        """Remaining=plain, success=green, cancel=faint, other-negative=yellow, failure=red."""
        assert select_color(None) is no_color   # not yet run
        assert select_color(0) is green         # success
        assert select_color(-1) is faint        # cancelled / SIGHUP
        assert select_color(-2) is yellow       # SIGINT
        assert select_color(-9) is yellow       # SIGKILL
        assert select_color(-15) is yellow      # SIGTERM
        assert select_color(-1001) is yellow    # template sentinel
        assert select_color(1) is red           # generic failure
        assert select_color(127) is red         # command not found

    def test_style_scheme(self) -> None:
        """Rich style names parallel select_color (for table rows)."""
        assert select_style(None) is None
        assert select_style(0) == 'green'
        assert select_style(-1) == 'dim'
        assert select_style(-15) == 'yellow'
        assert select_style(-1001) == 'yellow'
        assert select_style(1) == 'red'


@mark.integration
def test_plain_no_color_when_piped(temp_site: Path) -> None:
    """Without a color TTY, plain output carries no ANSI and no injected exit_status column."""
    taskfile = create_taskfile(temp_site, ['echo A  # HYPERSHELL: n:0'])
    assert main(['hs', 'submit', str(taskfile)])[0] == cli_status.success
    assert main(['hs', 'update', 'exit_status=0', '-t', 'n:0', '--no-confirm'])[0] == cli_status.success
    rc, out, _ = main(['hs', 'list', 'id', '-t', 'n:0'])
    assert rc == cli_status.success
    assert '\x1b[' not in out          # no color
    assert '\t' not in out             # single 'id' column, no injected exit_status leaked


@mark.integration
def test_plain_colorized_by_status(three_colored_tasks: Path) -> None:
    """Plain rows are wrapped in the exit-status color, showing only the requested field."""
    # id is not exit_status, so exit_status is injected for color then dropped from display.
    for tag, code in [('n:0', GREEN), ('n:1', FAINT), ('n:2', RED)]:
        rc, out, _ = main(['hs', 'list', 'id', '-t', tag])
        assert rc == cli_status.success
        assert out.startswith(code) and out.endswith('\x1b[0m')
        assert '\t' not in strip_ansi(out)   # only the id column is shown


@mark.integration
def test_table_colorized_by_status(three_colored_tasks: Path) -> None:
    """Table rows are styled by exit status; the injected exit_status column is not displayed."""
    rc, out, _ = main(['hs', 'list', 'id', '-f', 'table'])
    assert rc == cli_status.success
    assert GREEN in out and FAINT in out and RED in out
    assert 'exit_status' not in out          # injected column dropped from the table header


@mark.integration
def test_bare_list_prints_usage(temp_site: Path) -> None:
    """A bare `hs list` with no arguments prints usage rather than dumping the table."""
    taskfile = create_taskfile(temp_site, [f'echo {n}  # HYPERSHELL: n:{n}' for n in range(3)])
    assert main(['hs', 'submit', str(taskfile)])[0] == cli_status.success
    rc, _, _ = main(['hs', 'list'])
    assert rc != cli_status.success


@mark.integration
def test_small_result_needs_no_guard(temp_site: Path) -> None:
    """A result set at or under the threshold lists without --all."""
    taskfile = create_taskfile(temp_site, [f'echo {n}  # HYPERSHELL: n:{n}' for n in range(3)])
    assert main(['hs', 'submit', str(taskfile)])[0] == cli_status.success
    rc, out, _ = main_lines(['hs', 'list', 'id'])
    assert rc == cli_status.success
    assert len([line for line in out if line]) == 3


@mark.integration
def test_large_result_refused_without_intent(temp_site: Path) -> None:
    """Over DATABASE_TOO_BIG results is refused unless --all / --limit / --count is given."""
    count = DATABASE_TOO_BIG + 2
    taskfile = create_taskfile(temp_site, [f'echo {n}  # HYPERSHELL: k:{n}' for n in range(count)])
    assert main(['hs', 'submit', str(taskfile)])[0] == cli_status.success

    # Refused without an explicit intent.
    rc, _, err = main(['hs', 'list', 'id'])
    assert rc != cli_status.success
    assert str(count) in err or f'{count:,}' in err

    # --all dumps everything.
    rc, out, _ = main_lines(['hs', 'list', 'id', '--all'])
    assert rc == cli_status.success
    assert len([line for line in out if line]) == count

    # --limit bounds the result.
    rc, out, _ = main_lines(['hs', 'list', 'id', '--limit', '10'])
    assert rc == cli_status.success
    assert len([line for line in out if line]) == 10

    # --count is cheap and always allowed.
    rc, out, _ = main_lines(['hs', 'list', '--count'])
    assert rc == cli_status.success
    assert out == [str(count)]

    # A filter that narrows under the threshold needs no intent.
    rc, out, _ = main_lines(['hs', 'list', 'id', '-t', 'k:5'])
    assert rc == cli_status.success
    assert len([line for line in out if line]) == 1


@mark.integration
def test_broken_pipe_is_silent(temp_site: Path) -> None:
    """`hs list --all --csv | head` must exit cleanly, not dump a BrokenPipeError traceback."""
    if shutil.which('head') is None:
        skip('requires the `head` utility')
    # Enough rows that the CSV output exceeds the OS pipe buffer, so the writer is still
    # streaming when the reader (head) closes - reliably triggering EPIPE mid-write.
    taskfile = create_taskfile(temp_site, [f'echo {n}  # HYPERSHELL: k:{n}'
                                           for n in range(DATABASE_TOO_BIG + 500)])
    assert main(['hs', 'submit', str(taskfile)])[0] == cli_status.success

    lister = subprocess.Popen(['hs', 'list', '--all', '--csv'],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=os.environ)
    reader = subprocess.Popen(['head', '-n', '1'], stdin=lister.stdout, stdout=subprocess.DEVNULL)
    lister.stdout.close()          # only `head` holds the read end now
    reader.wait()
    err = lister.communicate()[1].decode()

    assert lister.returncode == cli_status.success
    assert 'BrokenPipeError' not in err
    assert 'CRITICAL' not in err
    assert 'Traceback' not in err and 'Exception ignored' not in err


@mark.integration
def test_all_limit_count_mutually_exclusive(temp_site: Path) -> None:
    """--all, --limit, and --count cannot be combined."""
    taskfile = create_taskfile(temp_site, ['echo A  # HYPERSHELL: n:0'])
    assert main(['hs', 'submit', str(taskfile)])[0] == cli_status.success
    assert main(['hs', 'list', '--all', '--limit', '5'])[0] != cli_status.success
    assert main(['hs', 'list', '--all', '--count'])[0] != cli_status.success
    assert main(['hs', 'list', '--limit', '5', '--count'])[0] != cli_status.success


@mark.integration
def test_signal_filter(temp_site: Path) -> None:
    """`--signal NAME` matches tasks recorded with that signal's exit_status (-N)."""
    taskfile = create_taskfile(temp_site, [f'echo {n}  # HYPERSHELL: n:{n}' for n in range(3)])
    rc, _, _ = main(['hs', 'submit', str(taskfile)])
    assert rc == cli_status.success

    # Simulate signal deaths recorded by the client (SIGTERM on n:0, SIGKILL on n:1).
    assert main(['hs', 'update', 'exit_status=-15', '-t', 'n:0', '--no-confirm'])[0] == cli_status.success
    assert main(['hs', 'update', 'exit_status=-9', '-t', 'n:1', '--no-confirm'])[0] == cli_status.success

    # --signal TERM selects exactly the -15 task, and it is n:0 (not n:1).
    rc, out, _ = main_lines(['hs', 'list', 'exit_status', '--signal', 'TERM'])
    assert rc == cli_status.success
    assert out == ['-15']
    assert main_lines(['hs', 'list', 'exit_status', '-t', 'n:0', '--signal', 'TERM'])[1] == ['-15']
    assert main_lines(['hs', 'list', 'exit_status', '-t', 'n:1', '--signal', 'TERM'])[1] == ['']

    # --signal accepts the SIG prefix / any case.
    assert main_lines(['hs', 'list', 'exit_status', '--signal', 'SIGKILL'])[1] == ['-9']

    # Nothing was killed by SIGHUP here.
    assert main_lines(['hs', 'list', 'exit_status', '--signal', 'hup'])[1] == ['']

    # Unknown signal name is a usage error.
    rc, _, _ = main(['hs', 'list', '--signal', 'BOGUS'])
    assert rc != cli_status.success


@mark.integration
def test_sighup_cancel_equivalence(temp_site: Path) -> None:
    """A SIGHUP death (exit_status == -1) is reported as cancelled: --signal HUP == --cancelled."""
    taskfile = create_taskfile(temp_site, [f'echo {n}  # HYPERSHELL: n:{n}' for n in range(2)])
    rc, _, _ = main(['hs', 'submit', str(taskfile)])
    assert rc == cli_status.success

    # A task whose process was terminated by SIGHUP lands on exit_status -1, same as --cancel.
    assert main(['hs', 'update', 'exit_status=-1', '-t', 'n:0', '--no-confirm'])[0] == cli_status.success

    by_signal = main_lines(['hs', 'list', 'exit_status', '-t', 'n:0', '--signal', 'HUP'])[1]
    by_cancelled = main_lines(['hs', 'list', 'exit_status', '-t', 'n:0', '--cancelled'])[1]
    assert by_signal == ['-1']
    assert by_cancelled == ['-1']
