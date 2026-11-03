Tasks may be assigned to an integer *group* for simple dependency management. Groups are
scheduled in ascending order: every task in the active group must reach a terminal state before
any task in the next group is scheduled. Within a group there is no ordering and all tasks may
run concurrently. Tasks default to group ``0``.

Assign a group at submit time with ``-g``/``--group``, or per task with an inline directive:

.. code-block:: shell

    echo 'stage-one.sh  # HYPERSHELL: group:0' | hs submit
    echo 'stage-two.sh  # HYPERSHELL: group:1' | hs submit

The server advances through groups automatically as each one completes. If a group cannot be
completed because it still holds failed tasks (after any ``--max-retries`` attempts are
exhausted), scheduling halts with a critical message; under ``--forever`` the scheduler instead
warns and waits for those tasks to be cleared or reverted (see ``hs update``). Use
``--max-retries`` and ``--eager`` to tune retry behavior within a group.

A task file given to ``hs cluster`` or ``hs server`` is read in full before scheduling begins, so
its groups run in ascending order whatever order the file lists them in. Input on standard input
or a pipe is scheduled as it arrives, which is best effort: a lower group that arrives after a
higher group has started runs once the current group finishes, and *HyperShell* logs a warning
naming both groups. The clients that ``hs cluster`` launches start once the file is read, so
their ``-T``/``--timeout`` clock does not run while it is read. A client connected to
``hs server`` whose ``-T``/``--timeout`` is shorter than the time to read the file reaches that
timeout and exits before any task is scheduled, and the server then waits for new clients.

Filter or update tasks by group with the ``-g``/``--group`` option of ``hs list`` and
``hs update``.
