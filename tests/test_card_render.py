# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the `card` view renderer (`render_card`, `card_width`, `STATUS_STYLES`)."""


# Type annotations
from __future__ import annotations

# Standard libs
import io
import re
from datetime import datetime

# External libs
from pytest import mark
from rich.console import Console
from rich.panel import Panel

# Internal libs
from hypershell.data.model import Task
from hypershell.task import render_card, card_width, CARD_MIN_WIDTH, CARD_MAX_WIDTH, STATUS_STYLES


UID = '3f2a9c1e-7b4d-4a2e-9c1e-7b4d4a2e9c1e'
FP = 'a1b2c3d4e5f60718293a4b5c6d7e8f90'
ANSI = re.compile(r'\x1b\[[0-9;]*m')
NOW = datetime(2026, 7, 31, 10, 12, 4)


def strip_ansi(text: str) -> str:
    """Remove ANSI SGR sequences."""
    return ANSI.sub('', text)


def make(**over) -> Task:
    """A transient Task with the columns the card reads; `tag` defaults to {} (never None)."""
    fields = dict(
        id=UID, group=4, part=0, fingerprint=FP, args='echo hello', command='echo hello',
        cores=1, memory=536870912, cores_max=0.5, memory_max=402653184.0,
        submit_host='login01', server_host='head', client_host='node042',
        schedule_time=NOW, start_time=NOW, completion_time=NOW, exit_status=0,
        source=None, tag={},
    )
    fields.update(over)
    return Task(**fields)


def render(task: Task, width: int, *, color: bool = False) -> str:
    """Render a card to a string at the given width (no color unless asked)."""
    buffer = io.StringIO()
    console = Console(width=width, file=buffer,
                      force_terminal=(True if color else None),
                      color_system=('standard' if color else None))
    console.print(render_card(task, width))
    return buffer.getvalue()


def max_line_width(text: str) -> int:
    """Widest visible (ANSI-stripped) line."""
    return max(len(line) for line in strip_ansi(text).splitlines())


@mark.unit
class TestCardRender:
    """The card renderer: content, responsiveness, clamping, status badge, no-color legibility."""

    def test_returns_panel(self) -> None:
        assert isinstance(render_card(make(), 100), Panel)

    def test_core_content_present(self) -> None:
        """Full id (never truncated), fingerprint, region titles, and the status badge appear."""
        out = strip_ansi(render(make(), 100))
        assert UID in out                       # id shown in full
        assert FP in out                        # fingerprint in the header
        for title in ('command', 'timing', 'resources', 'execution', 'output', 'retry', 'result'):
            assert title in out
        assert 'status: OK' in out

    def test_id_never_truncated_even_at_min_width(self) -> None:
        assert UID in strip_ansi(render(make(), 60))

    def test_width_clamped_to_range(self) -> None:
        """A tiny or huge terminal is clamped to [60, 160]."""
        assert card_width(10) == CARD_MIN_WIDTH == 60
        assert card_width(9999) == CARD_MAX_WIDTH == 160
        assert card_width(123) == 123
        assert max_line_width(render(make(), 40)) <= 60      # clamped up to the minimum
        assert max_line_width(render(make(), 300)) <= 160    # clamped down to the maximum

    def test_narrow_one_column(self) -> None:
        """Below 90 cols, regions stack: no single line holds two region titles."""
        lines = strip_ansi(render(make(), 70)).splitlines()
        assert not any('timing' in line and 'resources' in line for line in lines)

    def test_normal_two_columns(self) -> None:
        """90-149 cols: two columns — timing|resources share a line but execution does not."""
        lines = strip_ansi(render(make(), 100)).splitlines()
        assert any('timing' in line and 'resources' in line and 'execution' not in line
                   for line in lines)
        assert any('execution' in line and 'output' in line for line in lines)

    def test_wide_three_columns(self) -> None:
        """>=150 cols: three columns — timing, resources, AND execution share one line."""
        lines = strip_ansi(render(make(), 155)).splitlines()
        assert any(all(t in line for t in ('timing', 'resources', 'execution')) for line in lines)

    def test_header_horizontal_when_wide(self) -> None:
        """At >=130 the whole identity row (id, fp, group, part) sits on one line."""
        lines = strip_ansi(render(make(), 150)).splitlines()
        assert any(UID in line and FP in line and 'group 4' in line and 'part 0' in line
                   for line in lines)

    def test_header_stacks_id_when_narrow(self) -> None:
        """Below 130 the id gets its own line (never truncated, never crowded)."""
        lines = strip_ansi(render(make(), 100)).splitlines()
        id_line = next(line for line in lines if UID in line)
        assert 'part 0' not in id_line          # id is alone on its row
        out = '\n'.join(lines)
        assert 'group 4' in out and 'part 0' in out and FP in out

    def test_status_badge_lower_right(self) -> None:
        """R4: the status badge renders on the bottom border, right-aligned."""
        lines = [line for line in strip_ansi(render(make(), 100)).splitlines() if line.strip()]
        bottom = lines[-1]
        assert 'status: OK' in bottom
        assert bottom.index('status:') > len(bottom) // 2      # in the right half

    def test_status_palette_pinned(self) -> None:
        """R4: the three GOAL-fixed colors do not drift."""
        assert STATUS_STYLES['OK'] == 'bold green'
        assert STATUS_STYLES['FAILED'] == 'bold red'
        assert STATUS_STYLES['CANCELLED'] == 'yellow'

    def test_tags_region_present_and_omitted(self) -> None:
        """R3: the tags region shows key:value pairs, and is omitted when there are no tags."""
        with_tags = strip_ansi(render(make(tag={'priority': 'high'}), 100))
        assert 'tags' in with_tags and 'priority:high' in with_tags
        assert 'tags' not in strip_ansi(render(make(tag={}), 100))

    def test_markup_safe_values_do_not_crash_or_corrupt(self) -> None:
        """Regression: bracketed shell (rich markup) renders verbatim, never MarkupError."""
        for cmd in ("sed 's/x/[/]/'", 'echo [/red]', "awk '{a[b]=1}'", 'echo [bold]hi[/bold] ${arr[i]}'):
            out = strip_ansi(render(make(args=cmd, command=cmd, tag={'note': '[i]'}), 100))
            assert cmd in out                 # command shown exactly as the user wrote it
            assert 'note:[i]' in out          # tag brackets survive too

    @mark.parametrize('exit_status, schedule, complete, label', [
        (0, True, True, 'OK'),
        (1, True, True, 'FAILED'),
        (-1, True, True, 'CANCELLED'),
        (-9, True, True, 'KILLED'),
        (-1001, True, True, 'ERROR'),
        (None, True, False, 'RUNNING'),
        (None, False, False, 'WAITING'),
    ])
    def test_status_badge_reflects_lifecycle(self, exit_status, schedule, complete, label) -> None:
        task = make(exit_status=exit_status,
                    schedule_time=(NOW if schedule else None),
                    completion_time=(NOW if complete else None))
        assert task.status_label == label
        assert f'status: {label}' in strip_ansi(render(task, 100))

    def test_every_label_has_a_style(self) -> None:
        """Drift guard: every label `status_label` can emit maps to a rich style."""
        labels = {'OK', 'FAILED', 'CANCELLED', 'RUNNING', 'WAITING', 'ERROR', 'KILLED', 'UNKNOWN'}
        assert labels <= set(STATUS_STYLES)

    def test_no_color_when_not_a_terminal(self) -> None:
        """R6: piped/non-tty output drops ANSI but keeps the border and literal status text."""
        out = render(make(exit_status=1), 100, color=False)
        assert '\x1b[' not in out                # no color codes
        assert '─' in out and '│' in out         # box border still drawn
        assert 'status: FAILED' in out           # status readable as plain text

    def test_color_when_terminal(self) -> None:
        """On a real terminal the badge carries ANSI styling."""
        out = render(make(), 100, color=True)
        assert '\x1b[' in out
        assert 'status: OK' in strip_ansi(out)
