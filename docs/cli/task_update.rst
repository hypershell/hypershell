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

.. include:: ../_include/task_update_help.rst
