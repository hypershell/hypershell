.. _cli_task_info:

Info
====

Usage
-----

.. include:: ../_include/task_info_usage.rst

Description
-----------

.. include:: ../_include/task_info_desc.rst

|

.. versionadded:: 2.9.0
   ``card`` format (:pr:`60`).

.. versionchanged:: 2.9.0
   Times (``submit_time``, ``schedule_time``, ``start_time`` and ``completion_time``) are shown in
   the local time zone (``TZ``) with their UTC offset, as in ``2026-01-01 09:00:00-05:00``, in every
   format and on every database. Before, ``hs info`` showed a PostgreSQL time in the database server's
   time zone and a SQLite time without an offset. SQLite stores a time without its offset, so it is
   read as local time.

.. include:: ../_include/task_info_help.rst
