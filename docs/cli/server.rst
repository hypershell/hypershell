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

.. include:: ../_include/server_help.rst

.. _cli_server_sites:

Autoscaling with relay sites
----------------------------

.. versionadded:: 2.9.0

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
(see :ref:`Topology <cli_relay_topology>`).

.. versionchanged:: 2.9.0
   A superseded relay hands back the tasks its clients have not started, and exits.

For example, a server run in a container needs only its key and its settings in the environment:

.. code-block:: shell

    export HYPERSHELL_SERVER_AUTH=...
    export HYPERSHELL_AUTOSCALE_DISTRIBUTION=fill
    export HYPERSHELL_AUTOSCALE_SITES=east,west
    hs server --autoscaling=dynamic -H 0.0.0.0
