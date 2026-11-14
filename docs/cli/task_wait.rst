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
   ``-n``/``--interval`` takes a duration with units or a Slurm time, as ``-W`` does on
   :ref:`submit <cli_submit>`, and a signed value is refused.

.. include:: ../_include/task_wait_help.rst
