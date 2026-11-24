.. _cli_task_wait:

Wait
====

Usage
-----

.. include:: ../_include/task_wait_usage.rst

Description
-----------

.. include:: ../_include/task_wait_desc.rst

|

.. versionadded:: 2.9.0
   ``card`` format (:pr:`60`).

.. versionchanged:: 2.9.0
   :pr:`106`: ``-n``/``--interval`` takes a duration with units or a Slurm time, as ``-W`` does on
   :ref:`submit <cli_submit>`, and a signed value is refused.

.. versionchanged:: 2.9.0
   Each numeric option has a range, given with the option below. A value outside it, or one that is
   not a number of the option's kind, stops the command before it starts, with one line naming the
   option, the range and the value (exit status 2). ``-n 0`` is refused: it queried the database
   without rest. The least interval is one second.

.. include:: ../_include/task_wait_help.rst
