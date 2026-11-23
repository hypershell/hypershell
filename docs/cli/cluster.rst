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

.. versionchanged:: 2.9.0
   With ``--autoscaling``, a cluster stops and exits non-zero once ``autoscale.failed_launches``
   launched clients in a row (3 by default) have exited non-zero with no client registered,
   instead of relaunching them forever; ``0`` keeps relaunching. Each non-zero exit is warned
   about naming the client's process id and zone.

.. versionchanged:: 2.9.0
   With ``--autoscaling``, a check that finds the SQLite database locked is skipped with a warning
   and made again at the next period, instead of ending the run.

.. versionchanged:: 2.9.0
   A run fed by a stream (standard input, a pipe or a named pipe) ends once that input ends and
   every task is done, not once the tasks read so far are done, so a line that arrives after a pause
   still runs. Input that is never closed keeps the run going until it is.

.. versionchanged:: 2.9.0
   ``server.eager`` takes effect without ``--eager``, and ``server.attempts`` sets the retries
   without ``-r``; before, both keys were ignored. A value in a file for ``server.eager`` takes the
   boolean words, ``1``/``0`` included, and any other value stops the cluster before it starts.

.. versionchanged:: 2.9.0
   ``server.evict`` sets the eviction period in every mode; before, it was always 600 seconds. A
   value ``hs server`` refuses stops the cluster before it binds a port or launches a client.

.. versionchanged:: 2.9.0
   ``-R``/``--ratelimit`` takes a number (e.g., ``-R 0.5``), and defaults to ``client.ratelimit``
   in every mode; the undocumented ``task.ratelimit`` is no longer read. A fractional rate is
   passed to launched clients, which must be of this release or later. A rate other than ``0``
   under ``0.000001`` is refused before anything starts.

.. versionchanged:: 2.9.0
   ``--no-db`` leaves the configured database alone: it is not opened, created or initialized.

.. versionchanged:: 2.9.0
   ``-P``/``--period`` and ``autoscale.period`` must be greater than zero; zero or a negative
   value is refused.

.. versionchanged:: 2.9.0
   Each client with nothing left to run (no task running and none waiting to start) returns the
   results it holds at once, instead of waiting out ``-w``/``--bundlewait``. With work in hand it
   bundles as before.

.. versionchanged:: 2.9.0
   The result that finishes a run ends the scheduler's wait at once: a server whose input has ended
   no longer waits out its back-off (up to ``-Q``/``--poll``) after the last task. With
   ``--forever`` it keeps waiting, and nothing else about scheduling changes.

.. versionchanged:: 2.9.0
   A task that did not run, its template not expanding (``-1001``) or it needing more cores or
   memory than its client has (``-1002``), is never retried, and its group fails at once, as for a
   task with no retries left; before, with retries, it was retried until they ran out. A retry
   keeps its task's cores, memory and walltime; before, it ran with none of them.

.. versionchanged:: 2.9.0
   A line whose first non-blank character is ``#`` is a comment, and a ``#`` anywhere else is part
   of the command, run as written; earlier releases cut the command at the first ``#``. The
   ``# HYPERSHELL:`` marker is recognised anywhere on a line, so a command that must contain that
   text breaks it with shell quoting (``echo '#'' HYPERSHELL:'``). A task's group must be an
   integer: a *FILE* holding any other group value is refused whole, with nothing run, naming the
   value and its line, while on a stream only the offending line is refused, with an error naming
   it. A *FILE* that would submit a task below the database's reached group (with ``--restart``,
   ``--update`` or ``--repeat``, or a database kept from an earlier run) is refused before
   anything runs; tasks still in flight from an interrupted run do not count, and tasks skipped as
   already present are not checked. A stream's late lower group still runs, with a warning.

.. include:: ../_include/cluster_help.rst
