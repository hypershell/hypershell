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
   ``--from-json`` (:pr:`48`); ``--repeat`` and ``--update`` (:pr:`50`); ``--zone`` (:pr:`78`);
   ``--advertise``.

.. versionchanged:: 2.9.0
   :pr:`50`: ``--restart`` with a *FILE* submits only the tasks that never landed, and a file
   that was already submitted is refused unless ``--repeat`` or ``--update`` is given.

.. versionchanged:: 2.9.0
   :pr:`99`: with ``--autoscaling``, each launch names its client's zone to the launcher, as
   ``{zone}`` in the ``--launcher`` arguments and as ``HYPERSHELL_ZONE`` in its environment. A
   launcher that held a literal ``{zone}`` now receives the zone's name in its place. The
   autoscaler scales each zone with tasks in the active group on its own, under the same sizing
   options; ``--zone`` names the cluster's own zone, the one ``--init-size`` and ``--min-size``
   apply to.

.. versionchanged:: 2.9.0
   ``-w``/``--bundlewait``, ``-S``/``--signalwait``, ``-Q``/``--poll``, ``-P``/``--period`` and
   ``-d``/``--delay-start`` take a duration with units or a Slurm time, as ``-W`` does, and a
   signed value is refused except on ``--delay-start``, where a leading ``-`` still asks for a
   random delay (e.g., ``--delay-start=-10m``).

.. versionchanged:: 2.9.0
   Clients launched with ``--ssh`` or ``--autoscaling`` are told the bind address when ``-H``
   names one specific address, as with ``--mpi`` and ``--launcher``; only a server bound to
   *0.0.0.0* is advertised by this host's own name.

.. versionchanged:: 2.9.0
   With ``--ssh``, ``--mpi`` or ``--launcher``, a cluster whose launched processes have all exited,
   at least one non-zero, with tasks remaining, exits non-zero naming them, instead of waiting
   forever; each non-zero exit is warned about as it happens.

.. include:: ../_include/cluster_help.rst
