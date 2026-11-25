:orphan:

.. _cli_task_submit:

Submit
======

Usage
-----

.. include:: ../_include/task_submit_usage.rst

Description
-----------

.. include:: ../_include/task_submit_desc.rst

|

.. versionadded:: 2.9.0
   ``--zone`` (:pr:`78`).

.. versionchanged:: 2.9.0
   :pr:`125`: a resource tag given with ``-t``/``--tag`` outside its range is refused (exit status
   2) before anything is written.

.. include:: ../_include/task_submit_help.rst
