# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Integration tests for the ``hsx`` / ``hs cluster`` re-submission gate (R11-R16).

These drive the real cluster (embedded server + local client) so that de-duplicated and
novel tasks are not merely *submitted* but actually *run* — the scheduler must not stop a
run before a co-running submitter has committed its novel rows into a database that already
holds only completed tasks (see ``hypershell.server.Scheduler.submission_complete``).
"""


# Type annotations
from __future__ import annotations

# Standard libs
from pathlib import Path

# External libs
from pytest import mark
from cmdkit.app import exit_status

# Internal libs
from tests import main, main_lines, NO_OUTPUT, create_taskfile, create_taskfile_echo, assert_output


@mark.integration
def test_restart_no_flag_refuses_seen_file(temp_site: Path) -> None:
    """hsx with no gating flag detects and refuses a file already submitted (R11 == R5)."""
    taskfile = create_taskfile_echo(temp_site, count=4)

    # First run: a new file — submit all and run to completion.
    rc, stdout, stderr = main(['hsx', str(taskfile)])
    assert rc == exit_status.success, stderr
    assert sorted(stdout.split('\n')) == ['0', '1', '2', '3']
    assert main_lines(['hs', 'list', '--count']) == (exit_status.success, ['4'], NO_OUTPUT)

    # Second run, no flag: the (path, fingerprint) match is refused, naming the prior source.
    rc, stdout, stderr = main(['hsx', str(taskfile)])
    assert rc == exit_status.bad_argument
    assert stdout == ''
    assert_output(r'CRITICAL .* was already submitted as source .*', stderr, 1)
    # Refused before anything new is written — the count is unchanged.
    assert main_lines(['hs', 'list', '--count']) == (exit_status.success, ['4'], NO_OUTPUT)


@mark.integration
def test_restart_idempotent_across_requeues(temp_site: Path) -> None:
    """hsx FILE --restart is idempotent — requeuing the same job submits nothing new (R12)."""
    taskfile = create_taskfile_echo(temp_site, count=4)

    rc, stdout, stderr = main(['hsx', str(taskfile), '--restart'])
    assert rc == exit_status.success, stderr
    assert sorted(stdout.split('\n')) == ['0', '1', '2', '3']
    assert main_lines(['hs', 'list', '--count']) == (exit_status.success, ['4'], NO_OUTPUT)

    # Requeue: the fingerprint matches, every task is already present -> zero new tasks.
    rc, stdout, stderr = main(['hsx', str(taskfile), '--restart'])
    assert rc == exit_status.success, stderr
    assert stdout == ''
    assert main_lines(['hs', 'list', '--count']) == (exit_status.success, ['4'], NO_OUTPUT)


@mark.integration
def test_restart_refuses_changed_file(temp_site: Path) -> None:
    """hsx FILE --restart refuses a changed file at a seen path, suggesting --update (R12)."""
    taskfile = create_taskfile_echo(temp_site, count=4)
    rc, _, stderr = main(['hsx', str(taskfile), '--restart'])
    assert rc == exit_status.success, stderr

    # Same path, different content -> different source fingerprint -> refuse (alert + suggest).
    create_taskfile_echo(temp_site, count=6)  # overwrites task.in at the same path
    rc, stdout, stderr = main(['hsx', str(taskfile), '--restart'])
    assert rc == exit_status.bad_argument
    assert_output(r'CRITICAL .* differs from its prior submission.*--update', stderr, 1)
    # Nothing new landed on the refusal.
    assert main_lines(['hs', 'list', '--count']) == (exit_status.success, ['4'], NO_OUTPUT)


@mark.integration
def test_restart_update_alone_is_ambiguous(temp_site: Path) -> None:
    """hsx --update without --restart (or --repeat) is ambiguous and rejected (R13)."""
    taskfile = create_taskfile_echo(temp_site, count=2)
    assert main_lines(['hsx', str(taskfile), '--update']) == (
        exit_status.bad_argument, NO_OUTPUT, ['CRITICAL [hypershell] Using --update requires --restart']
    )


@mark.integration
def test_restart_update_adds_novel_and_runs(temp_site: Path) -> None:
    """hsx --update --restart records a new source and runs only the novel tasks (R14)."""
    taskfile = create_taskfile_echo(temp_site, count=4)
    rc, _, stderr = main(['hsx', str(taskfile), '--restart'])
    assert rc == exit_status.success, stderr
    assert main_lines(['hs', 'list', '--count']) == (exit_status.success, ['4'], NO_OUTPUT)

    # Extend the same file: the first four lines are byte-identical, two are new.
    create_taskfile_echo(temp_site, count=6)  # overwrites task.in at the same path
    rc, stdout, stderr = main(['hsx', str(taskfile), '--update', '--restart'])
    assert rc == exit_status.success, stderr
    # Only the two novel tasks run; the four already-present tasks are skipped (R18 tally).
    assert sorted(stdout.split('\n')) == ['4', '5']
    assert_output(r'INFO .* 4 tasks already present; submitting 2 new tasks$', stderr, 1)
    assert main_lines(['hs', 'list', '--count']) == (exit_status.success, ['6'], NO_OUTPUT)


@mark.integration
def test_restart_repeat_resubmits_all(temp_site: Path) -> None:
    """hsx --repeat records a new source and resubmits all tasks even on a match (R15)."""
    taskfile = create_taskfile_echo(temp_site, count=4)
    rc, _, stderr = main(['hsx', str(taskfile), '--restart'])
    assert rc == exit_status.success, stderr
    assert main_lines(['hs', 'list', '--count']) == (exit_status.success, ['4'], NO_OUTPUT)

    rc, stdout, stderr = main(['hsx', str(taskfile), '--repeat'])
    assert rc == exit_status.success, stderr
    # All four tasks run again; the database now holds two copies (two sources).
    assert sorted(stdout.split('\n')) == ['0', '1', '2', '3']
    assert main_lines(['hs', 'list', '--count']) == (exit_status.success, ['8'], NO_OUTPUT)


@mark.integration
def test_restart_update_repeat_contradictory(temp_site: Path) -> None:
    """hsx --update --repeat is contradictory and rejected before any work (R16)."""
    taskfile = create_taskfile_echo(temp_site, count=2)
    assert main_lines(['hsx', str(taskfile), '--update', '--repeat']) == (
        exit_status.bad_argument, NO_OUTPUT, ['CRITICAL [hypershell] Cannot combine --update with --repeat']
    )


@mark.integration
def test_restart_repeat_contradictory(temp_site: Path) -> None:
    """hsx --restart --repeat is contradictory (resume vs. fresh full run) and rejected."""
    taskfile = create_taskfile_echo(temp_site, count=2)
    assert main_lines(['hsx', str(taskfile), '--restart', '--repeat']) == (
        exit_status.bad_argument, NO_OUTPUT, ['CRITICAL [hypershell] Cannot combine --restart with --repeat']
    )


@mark.integration
def test_cluster_new_file_runs_against_completed_db(temp_site: Path) -> None:
    """A brand-new file runs even when the database already holds only completed tasks.

    Guards the scheduler start-race: with prior work all completed the database reads as
    ``remaining == 0``, so the scheduler must wait for the co-running submitter to commit the
    novel rows instead of stopping immediately.
    """
    first = create_taskfile_echo(temp_site, count=4)  # task.in -> run to completion
    rc, _, stderr = main(['hsx', str(first), '--restart'])
    assert rc == exit_status.success, stderr
    assert main_lines(['hs', 'list', '--count']) == (exit_status.success, ['4'], NO_OUTPUT)

    # Different path, new content: all tasks must actually run, not just get submitted.
    second = create_taskfile(temp_site, ['echo x', 'echo y'], filename='second.in')
    rc, stdout, stderr = main(['hsx', str(second)])
    assert rc == exit_status.success, stderr
    assert sorted(stdout.split('\n')) == ['x', 'y']
    assert main_lines(['hs', 'list', '--count']) == (exit_status.success, ['6'], NO_OUTPUT)
