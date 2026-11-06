.. _cli_cluster:

Cluster
=======

Usage
-----

.. include:: ../_include/cluster_usage.rst

Description
-----------

.. include:: ../_include/cluster_desc.rst

|

.. versionadded:: 2.9.0
   ``--from-json`` (:pr:`48`); ``--repeat`` and ``--update`` (:pr:`50`); ``--zone`` (:pr:`78`).

.. versionchanged:: 2.9.0
   :pr:`50`: ``--restart`` with a *FILE* submits only the tasks that never landed, and a file
   that was already submitted is refused unless ``--repeat`` or ``--update`` is given.

.. include:: ../_include/cluster_help.rst
