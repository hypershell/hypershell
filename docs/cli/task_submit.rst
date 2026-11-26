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

.. versionchanged:: 2.9.0
   :pr:`128`: ``-t``/``--tag`` and an inline directive take ``KEY=VALUE`` as well as ``KEY:VALUE``,
   with a likely typo of a field refused, and ``group``, ``zone``, ``cores``, ``memory`` and
   ``timeout`` set the task's fields; ``zone`` was stored as a tag. A field with no value, or
   ``none`` for any field but ``zone``, is refused.

.. include:: ../_include/task_submit_help.rst
