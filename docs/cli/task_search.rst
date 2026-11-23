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

.. include:: ../_include/task_search_help.rst
