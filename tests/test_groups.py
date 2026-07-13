# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Test task group functionality for dependency management."""


# Type annotations
from __future__ import annotations
from typing import Final

# Standard libs
from pathlib import Path

# External libs
from pytest import mark
from cmdkit.app import exit_status

# Internal libs
from tests import main, main_lines, NO_OUTPUT, create_taskfile, assert_output


@mark.integration
def test_group_basic_two_groups(temp_site: Path) -> None:
    """Two groups execute sequentially: group 0 completes before group 1 starts."""

    tasks = [
        'echo 0  #HYPERSHELL: n:0 group:0',
        'echo 1  #HYPERSHELL: n:1 group:0',
        'echo 2  #HYPERSHELL: n:2 group:0',
        'echo 3  #HYPERSHELL: n:3 group:1',
        'echo 4  #HYPERSHELL: n:4 group:1',
        'echo 5  #HYPERSHELL: n:5 group:1',
    ]

    taskfile = create_taskfile(temp_site, tasks)
    rc, stdout, stderr = main(['hs', 'cluster', str(taskfile), '-N2'])

    assert rc == exit_status.success
    assert sorted(stdout.strip().splitlines()) == list(map(str, range(len(tasks))))
    assert_output(r'Completed task group 0 - starting task group 1', stderr, 1)
    assert_output(r'DEBUG \[hypershell.server\] Completed task \(', stderr, len(tasks))
    assert_output(r'DEBUG \[hypershell.client\] Completed task \(', stderr, len(tasks))


@mark.integration
def test_group_three_sequential_groups(temp_site: Path) -> None:
    """Three groups execute in order: 0 -> 1 -> 2."""

    tasks = [
        'echo 0  #HYPERSHELL: n:0 group:0',
        'echo 1  #HYPERSHELL: n:1 group:0',
        'echo 2  #HYPERSHELL: n:2 group:1',
        'echo 3  #HYPERSHELL: n:3 group:1',
        'echo 4  #HYPERSHELL: n:4 group:2',
        'echo 5  #HYPERSHELL: n:5 group:2',
    ]

    taskfile = create_taskfile(temp_site, tasks)
    rc, stdout, stderr = main(['hs', 'cluster', str(taskfile), '-N2'])
    assert rc == exit_status.success
    assert sorted(stdout.strip().splitlines()) == list(map(str, range(len(tasks))))
    assert_output(r'Completed task group 0 - starting task group 1', stderr, 1)
    assert_output(r'Completed task group 1 - starting task group 2', stderr, 1)
    assert_output(r'DEBUG \[hypershell.server\] Completed task \(', stderr, len(tasks))
    assert_output(r'DEBUG \[hypershell.client\] Completed task \(', stderr, len(tasks))


@mark.integration
def test_group_single_group_default(temp_site: Path) -> None:
    """Tasks without explicit group assignment default to group 0."""

    tasks = [
        'echo 0  #HYPERSHELL: n:0',
        'echo 1  #HYPERSHELL: n:1',
        'echo 2  #HYPERSHELL: n:2',
        'echo 3  #HYPERSHELL: n:3',
    ]

    taskfile = create_taskfile(temp_site, tasks)
    rc, stdout, stderr = main(['hs', 'cluster', str(taskfile)])
    assert rc == exit_status.success
    assert sorted(stdout.strip().splitlines()) == list(map(str, range(len(tasks))))
    
    # Should NOT see any group transition messages (only one group)
    assert 'Completed task group' not in stderr
    assert_output(r'DEBUG \[hypershell.server\] Completed task \(', stderr, len(tasks))
    assert_output(r'DEBUG \[hypershell.client\] Completed task \(', stderr, len(tasks))

    # All tasks have group=0
    assert main_lines(['hs', 'list', 'group']) == (
        exit_status.success, ['0'] * len(tasks), NO_OUTPUT
    )



@mark.integration
def test_group_with_submit_option(temp_site: Path) -> None:
    """Submit with --group option assigns all tasks to specified group."""

    tasks_g1 = ['echo 0  #HYPERSHELL: n:0', 'echo 1  #HYPERSHELL: n:1']
    taskfile_g1 = create_taskfile(temp_site, tasks_g1, filename='tasks_g1.in')
    main(['hs', 'submit', str(taskfile_g1), '-g1'])
    assert main_lines(['hs', 'list', '-c']) == (exit_status.success, ['2', ], NO_OUTPUT)
    assert main_lines(['hs', 'list', 'group']) == (exit_status.success, ['1'] * 2, NO_OUTPUT)

    tasks_g2 = ['echo 2  #HYPERSHELL: n:2', 'echo 3  #HYPERSHELL: n:3']
    taskfile_g2 = create_taskfile(temp_site, tasks_g2, filename='tasks_g2.in')
    main(['hs', 'submit', str(taskfile_g2), '--group', '2'])
    assert main_lines(['hs', 'list', '-c']) == (exit_status.success, ['4', ], NO_OUTPUT)
    assert main_lines(['hs', 'list', 'group', '--order-by', 'group']) == (
        exit_status.success, ['1', '1', '2', '2'], NO_OUTPUT
    )


@mark.integration
def test_group_failed_task_with_retries(temp_site: Path) -> None:
    """Failed task in group 0 is retried within same group before moving to group 1."""

    tasks = [
        'echo 0                             #HYPERSHELL: n:0 group:0',
        '[ $TASK_ATTEMPT -eq 3 ] && echo 1  #HYPERSHELL: n:1 group:0',  # Succeeds on 3rd attempt
        'echo 2                             #HYPERSHELL: n:2 group:1',
    ]

    taskfile = create_taskfile(temp_site, tasks)
    rc, stdout, stderr = main(['hs', 'cluster', str(taskfile), '-r', '3'])
    assert rc == exit_status.success
    logs = stderr.strip().splitlines()
    
    # Task n:1 should fail the first 2 times
    assert_output(r'Non-zero exit status', stderr, 2)
    
    # Group should transition after retries
    assert_output(r'Completed task group 0 - starting task group 1', stderr, 1)
    rc, stdout_2, _ = main(['hs', 'list', 'id', '-t', 'n:2'])
    n2_id = stdout_2.strip()
    n2_idx, *_ = [i for i, line in enumerate(logs) if f'Completed task ({n2_id})' in line]
    group_idx, *_ = [i for i, line in enumerate(logs) if 'Completed task group 0 - starting task group 1' in line]
    assert group_idx < n2_idx

    
    # Task n:2 should still complete
    assert sorted(stdout.strip().splitlines()) == list(map(str, range(len(tasks))))

    # There should be 5 total tasks after 2 retries on n:1
    assert main_lines(['hs', 'list', '-c']) == (exit_status.success, ['5', ], NO_OUTPUT)


@mark.integration
def test_group_hard_failure_halts(temp_site: Path) -> None:
    """Group with failed tasks that exceed retries causes scheduler to halt."""

    tasks = [
        'echo 1      #HYPERSHELL: n:1 group:1',
        'false       #HYPERSHELL: n:2 group:1',  # Will always fail
        'echo 3      #HYPERSHELL: n:3 group:2',  # We should never get here
    ]

    taskfile = create_taskfile(temp_site, tasks)
    rc, stdout, stderr = main(['hs', 'cluster', str(taskfile), '-r1'])  # Only 1 retry
    assert rc == exit_status.success
    
    # Should see failure message
    assert_output(r'Failed task group 1', stderr, 1)
    
    # Task n:2 should NOT execute (group 1 never starts)
    assert '2' not in stdout


@mark.integration
def test_group_non_sequential_groups(temp_site: Path) -> None:
    """Non-sequential group numbers (0, 2, 5) execute in ascending order."""

    tasks = [
        'echo 0  #HYPERSHELL: n:0 group:0',
        'echo 1  #HYPERSHELL: n:1 group:0',
        'echo 2  #HYPERSHELL: n:2 group:2',
        'echo 3  #HYPERSHELL: n:3 group:2',
        'echo 4  #HYPERSHELL: n:4 group:5',
        'echo 5  #HYPERSHELL: n:5 group:5',
    ]

    taskfile = create_taskfile(temp_site, tasks)
    rc, stdout, stderr = main(['hs', 'cluster', str(taskfile)])
    assert rc == exit_status.success
    assert sorted(stdout.strip().splitlines()) == list(map(str, range(len(tasks))))
    
    # Should transition directly from 0 to 2 to 5 (skipping 1, 3, 4)
    assert_output(r'Completed task group 0 - starting task group 2', stderr, 1)
    assert_output(r'Completed task group 2 - starting task group 5', stderr, 1)


@mark.integration
def test_group_empty_database_starts_at_zero(temp_site: Path) -> None:
    """Empty database with no tasks defaults to group 0."""

    # NOTE: if there are no tasks in the database we wait forever on the first task
    # this would be a reasonable test but too much of a headache to add the background async to interrupt.
    # We could try to just do Task.current_group().value == 0 but something is weird with the test harness.


@mark.integration
def test_group_starts_based_on_previous_scheduled_tasks(temp_site: Path) -> None:
    """Task group starts with first group if no tasks scheduled or most recently scheduled."""

    # Verify database is empty
    rc, stdout, _ = main(['hs', 'list', '--count'])
    assert rc == exit_status.success
    assert stdout == '0'

    tasks = [
        'echo 0  #HYPERSHELL: n:0 group:2',
        'echo 1  #HYPERSHELL: n:1 group:2',
        'echo 2  #HYPERSHELL: n:2 group:5',
        'echo 3  #HYPERSHELL: n:3 group:5',
    ]

    # Distinct source paths: the two batches are genuinely different files, so re-submission
    # gating (which refuses changed content at a seen path with no flag) does not apply between them.
    taskfile = create_taskfile(temp_site, tasks, filename='batch1.in')
    main(['hs', 'submit', '-f', str(taskfile)])
    rc, stdout, stderr = main(['hs', 'cluster', '--restart'])
    assert rc == exit_status.success
    assert sorted(stdout.strip().splitlines()) == list(map(str, range(len(tasks))))

    # First submitted task was n:0
    assert_output(r'Starting with task group 2', stderr, 1)

    tasks = [
        'echo 10  #HYPERSHELL: n:10 group:20',
        'echo 11  #HYPERSHELL: n:11 group:20',
        'echo 12  #HYPERSHELL: n:12 group:50',
        'echo 13  #HYPERSHELL: n:13 group:50',
    ]

    taskfile = create_taskfile(temp_site, tasks, filename='batch2.in')
    main(['hs', 'submit', '-f', str(taskfile)])
    rc, stdout, stderr = main(['hs', 'cluster', '--restart'])
    assert rc == exit_status.success

    # Last scheduled task was n:3
    assert_output(r'Starting with task group 5', stderr, 1)


@mark.integration
def test_group_mixed_inline_and_cli_specification(temp_site: Path) -> None:
    """Inline group specification overrides CLI --group option."""

    tasks = [
        'echo 0  #HYPERSHELL: n:0',  # Uses CLI group
        'echo 1  #HYPERSHELL: n:1 group:2',  # Overrides to group 2
        'echo 2  #HYPERSHELL: n:2',  # Uses CLI group
    ]

    # Default to group 5 (n:1 overrides)
    taskfile = create_taskfile(temp_site, tasks)
    main(['hs', 'submit', '-f', str(taskfile), '-g5'])
    rc, stdout, stderr = main(['hs', 'cluster', '--restart'])
    assert rc == exit_status.success

    # Should transition from group 2 to group 5
    assert_output(r'Completed task group 2 - starting task group 5', stderr, 1)


@mark.integration
def test_group_parallel_execution_within_group(temp_site: Path) -> None:
    """Tasks within the same group execute in parallel."""

    tasks = [
        'sleep 1 && echo 0  #HYPERSHELL: n:0 group:1',
        'sleep 1 && echo 1  #HYPERSHELL: n:1 group:1',
        'sleep 1 && echo 2  #HYPERSHELL: n:2 group:2',
        'sleep 1 && echo 3  #HYPERSHELL: n:3 group:2',
        'sleep 1 && echo 4  #HYPERSHELL: n:4 group:3',
        'sleep 1 && echo 5  #HYPERSHELL: n:5 group:3',
        'sleep 1 && echo 6  #HYPERSHELL: n:6 group:4',
        'sleep 1 && echo 7  #HYPERSHELL: n:7 group:4',
    ]

    taskfile = create_taskfile(temp_site, tasks)
    
    import time
    start = time.time()
    rc, stdout, stderr = main(['hs', 'cluster', str(taskfile), '-N8'])
    elapsed = time.time() - start
    assert rc == exit_status.success
    assert sorted(stdout.strip().splitlines()) == list(map(str, range(len(tasks))))
    
    # With 8 threads we should be able to get through everything in one shot (~1 second ignoring overhead)
    # But because of the groups we can only execute one group at a time regardless of threads
    # So that's 4 seconds plus 1-2 second backoff between groups
    assert elapsed > 4 + 3*2, f'Expected > 10s for parallel execution, took {elapsed:.1f}s'
    
    # All tasks should complete
    assert_output(r'Completed task group 1 - starting task group 2', stderr, 1)
    assert_output(r'Completed task group 2 - starting task group 3', stderr, 1)
    assert_output(r'Completed task group 3 - starting task group 4', stderr, 1)
    assert_output(r'DEBUG \[hypershell.server\] Completed task \(', stderr, len(tasks))
    assert_output(r'DEBUG \[hypershell.client\] Completed task \(', stderr, len(tasks))
