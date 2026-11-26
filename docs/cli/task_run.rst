.. _cli_task_run:

Run
===

Usage
-----

.. include:: ../_include/task_run_usage.rst

Description
-----------

.. include:: ../_include/task_run_desc.rst

|

.. versionchanged:: 2.9.0
   :pr:`106`: ``-n``/``--interval`` takes a duration with units or a Slurm time, as ``-W`` does on
   :ref:`submit <cli_submit>`, and a signed value is refused.

.. versionchanged:: 2.9.0
   :pr:`125`: each numeric option has a range. A value outside it, or
   one that is not a number of the option's kind, stops the command before it starts, with one line
   naming the option, the range and the value (exit status 2). ``-n 0`` is refused: it queried the
   database without rest. The least interval is one second.

.. versionchanged:: 2.9.0
   :pr:`125`: a resource tag given with ``-t``/``--tag`` outside its range is refused (exit status
   2) before anything is written; before, it was stored as given, or stopped the command with an
   error from the database.

.. versionchanged:: 2.9.0
   ``-t`` and an inline directive take ``KEY=VALUE`` as well as ``KEY:VALUE``; ``zone`` sets the
   task's zone, recorded in the database, where it was stored as a tag; with ``=`` a likely typo of
   a task column is refused (exit status 2 for ``-t``). ``cores:none``, ``memory:none`` and
   ``timeout:none`` are refused, where they cleared the option's value for that task, and so is a
   field with no value; a bare ``cores`` was stored as empty.

.. include:: ../_include/task_run_help.rst
