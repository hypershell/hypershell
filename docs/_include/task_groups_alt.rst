Tasks may be assigned to a non-negative integer *group* for simple dependency management. Groups are
scheduled in ascending order: every task in the active group must reach a terminal state before
any task in the next group is scheduled. Within a group there is no ordering and all tasks may
run concurrently. Tasks default to group ``0``.

Assign a group at submit time with ``-g``/``--group``, or per task with an inline directive:

.. code-block:: shell

    echo 'stage-one.sh  # HYPERSHELL: group:0' | hs submit -
    echo 'stage-two.sh  # HYPERSHELL: group:1' | hs submit -

The directive takes ``group=1`` as well as ``group:1``: either separator sets the group, never a tag
(see ``hs submit``). With ``=``, a likely typo such as ``grop=1`` is refused, naming ``group``.

.. versionchanged:: 2.9.0
   ``group=N`` sets the group, as ``group:N`` does; it was refused as a malformed tag.

A group is a non-negative integer, written as ``-g``/``--group`` takes it (``7``, ``+5`` and
``07`` are accepted). Any other value, including a group below 0, is refused wherever it arrives: an
inline ``group:`` tag, ``-t group:...`` or the ``group`` key of a ``--from-json`` record, which must
be a JSON integer (``"7"``, ``3.0``, ``true`` and ``null`` are refused). Input read in full (a task
file given to ``hs submit``, ``hs cluster`` or ``hs server``, a ``--from-json`` source, a command given
as arguments, and standard input to ``hs submit`` with the database) is refused whole, with nothing
submitted or run, exit status 5 and an error naming the value and its line or record. On a stream
into ``hs cluster``, ``hs server`` or ``hs submit --queue``, only the offending line is refused, with
an error naming its value and line; the lines after it run as they would without it, so a refused
tag-only line sets nothing. ``-t group:x`` exits with status 2 before any input is read.

.. versionchanged:: 2.9.0
   :pr:`118`: a group that is not an integer is refused. Earlier releases stored such values as
   given on SQLite and ran them in an undocumented order, and ``group:nan`` hung the run.

.. versionchanged:: 2.9.0
   A group below 0 is refused, as any other value outside the range is. Earlier releases accepted
   a negative group; a database that already holds one still runs it first.

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
A run fed by a stream keeps scheduling until that input ends, so a stream with long pauses wants
clients with no ``-T``/``--timeout`` (or autoscaling): a client that times out during a pause is
not there to run the lines that follow it.

The *reached group* is the highest group the current run has started, read from the tasks in the
database: a run is the work since the database last held no unfinished task, so once every task has
finished there is no reached group and any group is accepted. Cancelled tasks do not count, and a
failure counts as finished unless its retry has been created. With the database, ``hs submit``
refuses a submission holding a task below the reached group, all or nothing, with exit status 2 and
an error naming the lowest such group and the reached group; it reads standard input in full first.
A submission that names no group takes the reached group when the most recently scheduled group is
below it. ``hs cluster`` and ``hs server`` refuse a task file the same way when the database has a
reached group (with ``--restart``, ``--update`` or ``--repeat``, or a database kept from an earlier
run), before anything is submitted or run. For them, tasks still in flight from an interrupted run
do not count, because the server returns them to the queue when it starts, and tasks the
re-submission gate skips as already present are not checked, so a same-file ``--restart`` of a run
that was interrupted is never refused. A stream is not refused: its late lower group runs, with the
warning above. To go on, submit at the reached group or higher, wait for the run to finish, or use a
fresh database.

The reached group is read from the tasks' own times, so it can fall short of the group a live server
has truly started (for example when every task of a higher group finished before the oldest task
still open was submitted, or while a failure waits for its retry to be created); a lower group
accepted then runs late, with the warning above. It can overshoot only when those times mislead:
after ``hs update --revert`` of a finished task, across clocks that disagree between hosts, or on
SQLite across the hour a daylight-saving change repeats.

A line holding a ``#`` after its first non-blank character is a different task in 2.9.0 than it was
before (earlier releases cut the command there), so ``--restart`` or ``--update`` of a file first
submitted by an earlier release submits such lines again, and on a database with a reached group a
file whose such line is in a lower group is refused. An old task stored cut that failed keeps its
group failed until it is cancelled or updated with ``hs update``.

.. versionchanged:: 2.9.0
   :pr:`118`: a task below the reached group is refused by ``hs submit``, and a task file holding
   one by ``hs cluster`` and ``hs server``; ``hs submit`` reads standard input in full before
   submitting.

Filter or update tasks by group with the ``-g``/``--group`` option of ``hs list`` and
``hs update``.
