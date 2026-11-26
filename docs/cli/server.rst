.. _cli_server:

Server
======

Usage
-----

.. include:: ../_include/server_usage.rst

Description
-----------

.. include:: ../_include/server_desc.rst

|

.. versionchanged:: 2.9.0
   :pr:`78`: clients request their bundles; ``-b``/``--bundlesize`` applies to a client that asks
   for no size of its own, and ``server.bundlemax`` caps every reply.

.. versionchanged:: 2.9.0
   :pr:`104`: a key on the command line is refused; the server takes it from the environment or a
   configuration file.

.. versionchanged:: 2.9.0
   :pr:`105`: ``--initdb`` initializes the database at launch, as for ``hs submit`` and
   ``hs cluster``, and ``--no-db`` leaves the configured database alone.

.. versionchanged:: 2.9.0
   :pr:`106`: ``-w``/``--bundlewait``, ``-Q``/``--poll`` and ``-P``/``--period`` take a duration
   with units or a Slurm time, as ``-W`` does, and a signed value is refused.

.. versionchanged:: 2.9.0
   :pr:`117`: ``server.eager`` takes effect without ``--eager``; before, the key was ignored. A
   value in a file takes the boolean words, ``1``/``0`` included, and any other value stops the
   server before it starts.

.. versionchanged:: 2.9.0
   :pr:`110`: a run fed by a stream (standard input, a pipe or a named pipe) ends once that input
   ends and every task is done, not once the tasks read so far are done, so a line that arrives
   after a pause still runs. Input that is never closed keeps the run going until it is.

.. versionchanged:: 2.9.0
   :pr:`112`: ``-P``/``--period`` and ``autoscale.period`` must be greater than zero; zero or a
   negative value is refused.

.. versionchanged:: 2.9.0
   :pr:`115`: the result that finishes a run ends the scheduler's wait at once: a server whose input
   has ended no longer waits out its back-off (up to ``-Q``/``--poll``) after the last task. With
   ``--forever`` it keeps waiting, and nothing else about scheduling changes.

.. versionchanged:: 2.9.0
   :pr:`119`: a task that did not run, its template not expanding (``-1001``) or it needing more
   cores or memory than its client has (``-1002``), is never retried, and its group fails at once,
   as for a task with no retries left; before, with retries, it was retried until they ran out. A
   retry keeps its task's cores, memory and walltime; before, it ran with none of them.

.. versionchanged:: 2.9.0
   :pr:`118`: a line whose first non-blank character is ``#`` is a comment, and a ``#`` anywhere
   else is part of the command, run as written; earlier releases cut the command at the first ``#``.
   The ``# HYPERSHELL:`` marker is recognised anywhere on a line, so a command that must contain
   that text breaks it with shell quoting (``echo '#'' HYPERSHELL:'``). A task's group must be a
   non-negative integer: a *FILE* holding any other group value is refused whole, with nothing run, naming the
   value and its line, while on a stream only the offending line is refused, with an error naming
   it. With a database kept from an earlier run, a *FILE* that would submit a task below its reached
   group is refused before anything runs; tasks still in flight from an interrupted run do not
   count. A stream's late lower group still runs, with a warning.

.. versionchanged:: 2.9.0
   :pr:`125`: each numeric option has a range. A value outside it, or
   one that is not a number of the option's kind, stops the command before it starts, with one line
   naming the option, the range and the value (exit status 2). An option with an advisory limit
   takes a value past it and runs, with one warning.

.. versionchanged:: 2.9.0
   An inline directive takes ``KEY=VALUE`` as well as ``KEY:VALUE``, and ``zone`` sets the task's
   zone, recorded in the database, where it was stored as a tag. With ``--no-db``, or no database
   configured, a zone in the input is refused: a *FILE* exits 5 before the server starts, a stream
   refuses that line. ``cores:none``, ``memory:none`` and ``timeout:none`` are refused, where they
   cleared the option's value for that task, and so is a field with no value, where it was stored
   as empty.

.. include:: ../_include/server_help.rst

.. _cli_server_sites:

Autoscaling with relay sites
----------------------------

.. versionadded:: 2.9.0
   :pr:`100`

With ``--autoscaling``, the server keeps no launcher of its own. It can run anywhere its relays can
reach, off-cluster or in a container, and the capacity comes from the :ref:`relays <cli_relay>`
connected to it. Any relay that connects with the server's key takes part; the server needs no list
of sites.

Each relay advertises its site (the relay host's name unless its ``site`` is set), the zones it
serves and, for each zone, the ``min``, ``max`` and ``init`` clients configured beside that zone's
launcher at the relay. At each check the server:

- reads each zone's work in the active *task group*, exactly as ``hs cluster --autoscaling`` does,
  so a zone whose tasks wait in a later group gets no growth yet;
- holds each site's floor in each zone, its ``min`` (and its ``init`` while it starts), even while
  the zone's tasks wait in a later group;
- grows a zone by its demand, at most one client per check, never past the sum of its live sites'
  ``max``, and places each new client on a site that serves the zone by the distribution;
- publishes the result as a new version when it changed. Each relay fetches its own entries at its
  heartbeat and launches what is asked of it with its own launcher.

The distribution is ``autoscale.distribution``:

*spread* (the default)
    Each new client of a zone goes to the live site serving it that runs the fewest of them.

*fill*
    Each new client goes to the first site in preference order that is below its ``max``, so one
    site is saturated before the next gets any. The order is ``autoscale.sites`` (listed sites
    first, as listed), then every other live site in the order it first registered. Use it to
    prefer the site whose service units cost less.

Under the *fixed* policy, a zone with work gets one client when none runs, and its size otherwise
comes from the relays' ``min`` and ``init``; use *dynamic* to grow with task pressure. The server's
own ``autoscale.size`` and ``autoscale.launcher`` do not apply. Nothing is ever terminated to
reconcile a lower target: the relay launches no more, and its extra clients leave by their idle
timeout. A relay with no clients stays connected, ready to grow.

A site whose relay is evicted drops out of the plan. Its unfinished tasks are put back as for any
evicted client, and under *dynamic* its share regrows at the remaining sites, one client per check.
When two live relays advertise the same site, the one that registered last holds the site's entries
for every zone it serves, the older one is asked for none in any zone (a zone only the older one
serves goes to the other sites that serve it), and the server logs a warning naming both. The newer
relay is planned on its own clients alone, so a relay restarted after a crash is planned at once
rather than after its predecessor's eviction; if the older relay is in fact still running, its
clients run beside the newer relay's until they leave by their idle timeout. The older relay is
also told it is superseded: it asks for no more work, hands back the tasks none of its clients has
started, which the server puts back in the queue at once, and exits once its clients have finished
(see :ref:`Topology <cli_relay_topology>`). A relay that says on its heartbeat that it is retiring
never holds its site, in whatever order it registered.

.. versionchanged:: 2.9.0
   :pr:`101`: a superseded relay hands back the tasks its clients have not started, and exits.

For example, a server run in a container needs only its key and its settings in the environment:

.. code-block:: shell

    export HYPERSHELL_SERVER_AUTH=...
    export HYPERSHELL_AUTOSCALE_DISTRIBUTION=fill
    export HYPERSHELL_AUTOSCALE_SITES=east,west
    hs server --autoscaling=dynamic -H 0.0.0.0
