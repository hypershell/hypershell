.. _cli_task_update:

Update
======

Usage
-----

.. include:: ../_include/task_update_usage.rst

Description
-----------

.. include:: ../_include/task_update_desc.rst

|

.. versionadded:: 2.9.0
   ``-X``/``--cancelled`` and ``--signal`` (:commit:`27db1b0`).

.. versionchanged:: 2.9.0
   :pr:`111`: every change given lands together. With ``--limit``, a field assignment, ``--cancel``
   or ``--revert`` with tag changes or ``--remove-tag`` are all applied; before, only the first kind
   was. Without ``--limit``, tag changes and ``--remove-tag`` reach the tasks that a field change,
   ``--cancel`` or ``--revert`` takes out of the filter; before, those got the field change only.

.. versionchanged:: 2.9.0
   :pr:`111`: with ``--limit``, a selected task is written only if it still matches the filter at
   the time of the write, and every selected task in one transaction. A task another process changed
   so that it no longer matches is left as it is and not counted; before, it was overwritten (a
   ``--cancel`` could discard a result that had landed meanwhile).

.. versionchanged:: 2.9.0
   :pr:`111`: `Updated N tasks` and `Deleted N tasks` count the tasks actually written, not the
   count taken at the prompt. When fewer, a warning says how many tasks no longer matched and were
   left as they are; the exit status is 0.

.. versionchanged:: 2.9.0
   :pr:`111`: a task the filter still selects that cannot be written after the retries, another
   writer holding it throughout, makes the update write nothing at all, with or without ``--limit``:
   it logs an error naming the task, saying the update was contended and nothing was written, and
   exits with status 5, with no traceback file. Before, ``--limit`` left a partial update and a
   traceback file, and the form without it skipped the task and counted it anyway.

.. versionchanged:: 2.9.0
   :pr:`111`: on a TimescaleDB hypertable, a task another writer held while the update waited is
   written and counted if it still matches once that writer commits; before, it could be skipped,
   stop a ``--limit`` update part way, or be reported not found.

.. versionchanged:: 2.9.0
   :pr:`111`: the single-task form, ``hs update ID FIELD VALUE``, reports the task not found (exit
   status 5) only when no task has that ID.

.. versionchanged:: 2.9.0
   :pr:`119`: ``--delete`` with a ``FIELD=VALUE``, a ``KEY:VALUE`` or ``--remove-tag`` exits with
   status 2 before it counts or asks, and deletes nothing; before, the tasks were deleted and the
   changes ignored. The single-task form ``hs update ID FIELD VALUE`` with ``--cancel``,
   ``--revert``, ``--delete`` or ``--remove-tag`` exits with status 2 and writes nothing; before, it
   wrote the field and ignored the option.

.. versionchanged:: 2.9.0
   A group change, ``group=VALUE`` or ``ID group VALUE`` in the single-task form, takes an integer
   as ``hs submit -g`` does, in the 32-bit range. Any other value exits with status 2 before anything
   is counted, asked or written, and no other change given with it is written. Before, a text,
   fractional or out-of-range group was stored on SQLite, ``1e3`` and ``true`` were stored as 1000
   and 1, and ``none``, ``null`` and ``nan`` failed with exit status 5 and a traceback. The
   single-task form ``hs update ID FIELD VALUE`` with an option that selects tasks (``-w``, ``-t``,
   ``-g``, ``-l``, ``-s``, ``--desc``, ``-F``, ``-S``, ``-C``, ``-R``, ``-X``, ``--retries`` or
   ``--signal``) exits with status 2, naming each, and writes nothing; before, it ignored the options
   and wrote the task. To write a task only if it matches, use the filtered form,
   ``hs update FIELD=VALUE -w "id == ID" -F``.

.. versionchanged:: 2.9.0
   :pr:`111`: on SQLite, ``-t``/``--with-tag`` with several tag names and no values requires every
   one of them, as on PostgreSQL; before, only the last name was checked. ``hs list`` is fixed the
   same way.

.. versionchanged:: 2.9.0
   Each numeric option has a range, given with the option below. A value outside it, or one that is
   not a number of the option's kind, stops the command before it starts, with one line naming the
   option, the range and the value (exit status 2). ``-l``/``--limit`` below 1 is refused, and so is
   a ``-g``/``--group`` outside 32 bits.

.. versionchanged:: 2.9.0
   Every column ``hs update`` writes is read by its kind, in both forms and on SQLite and PostgreSQL
   alike. ``cores``, ``memory`` and ``timeout`` are read as ``hs submit -c``, ``-m`` and ``-W`` read
   them, so ``memory=4G`` and ``timeout=1:30:00`` store bytes and seconds; ``waited`` and
   ``duration`` take durations; ``exit_status``, ``attempt`` and ``part`` take whole numbers, and
   ``cores_max`` and ``memory_max`` numbers, each within the range the description lists (the
   exit status and peak ranges are new). Any other value, ``none`` for ``attempt`` or ``part``, and
   ``ID id VALUE`` exit with status 2 before anything is counted, asked or written. Before, SQLite
   stored text, fractions and out-of-range numbers, ``true`` was stored as 1, and PostgreSQL failed
   with exit status 5 and a traceback (rounding a fraction instead). ``cores`` above 1,024 and
   ``attempt`` above 100 are written with a warning.

.. versionchanged:: 2.9.0
   ``retried`` takes the boolean words a configuration key takes, ``yes``/``no`` and ``on``/``off``
   as well as ``true``/``false`` and ``1``/``0``, in any case. The uuid columns take the 8-4-4-4-12
   form only, stored in lower case on both databases. ``zone`` takes a zone name, as
   ``hs submit --zone`` reads it: ``zone=default`` and ``zone=none`` put the task in the default
   zone, and a new name is recorded so ``hs list`` and ``hs info`` show it. ``none`` for ``args``,
   ``submit_id`` or ``retried`` exits with status 2. Before, SQLite stored any text in a uuid or
   zone column, a zone name failed with exit status 5 on PostgreSQL, the default zone's reserved id
   was stored where its tasks are matched by none, and ``none`` for these columns failed with exit
   status 5 and a traceback.

.. versionchanged:: 2.9.0
   ``submit_time``, ``schedule_time``, ``start_time`` and ``completion_time`` take ISO 8601 on both
   databases, a value without an offset being local time, and hold the same instant on SQLite and
   PostgreSQL. Before, SQLite refused every value with exit status 5 and a traceback, and PostgreSQL
   stored whatever its server read, a time without an offset in the server session's zone. ``now``,
   ``today`` and ``epoch``, which PostgreSQL stored, exit with status 2: use ``$(date -Iseconds)``.
   ``none`` for ``submit_time`` exits with status 2.

.. versionchanged:: 2.9.0
   ``KEY=VALUE`` with a key that is not a task column is a tag change, as ``KEY:VALUE`` is, in both
   forms (``hs update ID KEY VALUE`` too). Before, it exited with status 2 (``Unrecognized task
   field``, ``Invalid field name``). Without ``-f``, the prompt lists each change as a column, a tag
   or a removal before asking. A key one edit from a column, letter case aside (``exit_stauts=0``,
   ``Exit_Status=0``, ``tags=x``), exits with status 2, naming the key and the nearest column, and
   writes nothing; ``KEY:VALUE`` always sets a tag. ``tag=VALUE`` exits with status 2, pointing to
   ``KEY=VALUE``, ``KEY:VALUE`` and ``--remove-tag``; before, it replaced the task's every tag with
   the value as JSON text. An argument is split at its first ``=`` or ``:``. Every tag is checked by
   the tag rules: a filtered ``KEY:VALUE`` that breaks them exits with status 2 instead of 5 with a
   traceback, and ``hs update ID tag KEY:VALUE``, which stored any tag before, is checked too.

.. include:: ../_include/task_update_help.rst
