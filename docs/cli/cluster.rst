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

.. versionchanged:: 2.9.0
   With ``--autoscaling``, each launch names its client's zone to the launcher, as ``{zone}`` in
   the ``--launcher`` arguments and as ``HYPERSHELL_ZONE`` in its environment. A launcher that
   held a literal ``{zone}`` now receives the zone's name in its place. The autoscaler scales
   each zone with tasks in the active group on its own, under the same sizing options; ``--zone``
   names the cluster's own zone, the one ``--init-size`` and ``--min-size`` apply to.

.. include:: ../_include/cluster_help.rst
