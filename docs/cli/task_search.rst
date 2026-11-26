.. _cli_task_search:

List
====

Usage
-----

.. include:: ../_include/task_search_usage.rst

Description
-----------

.. include:: ../_include/task_search_desc.rst

|

.. versionadded:: 2.9.0
   ``--all`` (:commit:`961fb22`); ``--part`` (:pr:`59`); ``card`` format (:pr:`60`);
   ``-X``/``--cancelled`` and ``--signal`` (:commit:`27db1b0`).

.. versionchanged:: 2.9.0
   :commit:`961fb22`: more than 1,000 results are refused without ``--all``, ``--limit`` or
   ``--count``. :commit:`210a93e`: output to a terminal is colorized by exit status.

.. versionchanged:: 2.9.0
   :pr:`111`: on SQLite, ``-t``/``--with-tag`` with several tag names and no values requires every
   one of them, as on PostgreSQL; before, only the last name was checked.

.. versionchanged:: 2.9.0
   :pr:`125`: each numeric option has a range. A value outside it, or
   one that is not a number of the option's kind, stops the command before it starts, with one line
   naming the option, the range and the value (exit status 2). ``-l``/``--limit`` below 1 is
   refused, and so are a ``-g``/``--group`` or ``--part`` outside 32 bits.

.. include:: ../_include/task_search_help.rst
