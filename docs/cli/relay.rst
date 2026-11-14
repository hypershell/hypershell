.. _cli_relay:

Relay
=====

.. versionadded:: 2.9.0
   :pr:`82`

Usage
-----

.. include:: ../_include/relay_usage.rst

Description
-----------

.. include:: ../_include/relay_desc.rst

.. versionchanged:: 2.9.0
   a relay whose TLS setting differs from its upstream *server*'s exits at once, with an error.

.. versionchanged:: 2.9.0
   at an upstream shutdown, a zone with no *client* at the site asks once more, and takes what is
   still staged for it, unless an upstream *server* autoscaling with relay sites plans no *client*
   for it at this relay, and hands it back if that *server* stops planning one before a *client* starts.

|

.. include:: ../_include/relay_help.rst


.. _cli_relay_topology:

Topology
--------

.. versionadded:: 2.9.0
   :pr:`100`

Each zone a relay serves has client bounds: ``min``, ``max`` and ``init``, set for every zone in
``[relay]`` or for one zone in its ``[relay.zone.NAME]`` table, beside its launcher. Every heartbeat
of the zone's identity advertises them upstream, with the *clients* launched in the zone now.

.. code-block:: toml

    [relay]
    site = "east"

    [relay.zone.cpu]
    max = 8

    [relay.zone.gpu]
    launcher = "srun --partition={zone}"
    min = 1
    max = 2

``min``
    Fewest *clients* the zone is assigned, even while its tasks wait in a later *task group*
    (default: 0, so a zone with no work gets none).

``init``
    *Clients* the zone is assigned when the relay starts (default: ``min``).

``max``
    Most *clients* the zone is assigned (default: the size, ``-N`` or the zone's ``size``, which
    is 1 unless set: set ``max`` to let a zone grow).

Against an upstream *server* autoscaling with relay sites (:ref:`hs server --autoscaling
<cli_server_sites>`), the server plans every zone's *clients* over the relays that serve it, within
these bounds, and publishes the result. The relay reads it after every heartbeat, and once before
its site starts. For each zone it follows one rule: it launches the server's target less the count
the server's plan is known to rest on, or that zone's running *clients* if more, each time it learns
that count, and at no other time. A new plan states the count the server saw. While the plan stands
unchanged and the zone runs fewer *clients* than its target, the count is the zone's running
*clients* themselves, once the server has planned twice more since a heartbeat reported them: a
*client* that leaves before any heartbeat counts it, or after the server counted it but before the
relay read the plan made with it, changes nothing the server publishes. So a *client* that exits is
launched again only when the server, having planned on the exit, still asks for it; the relay never
launches beyond the target, and a server that plans less often than the relay checks is never taken
for one that lost count. It never ends a *client* to reconcile: a lowered target, or a target of
zero, takes effect as the zone's *clients* exit after ``relay.timeout`` idle seconds. With no
*clients* left the relay stays connected, ready to grow. A raised bound lets a later plan assign
more; bounds are read when the relay starts, so changing them means restarting it.

When a relay restarts while its old process is still running, both advertise the same site, and
the upstream *server* assigns the site's *clients* to the newer one only. It also marks the old
process superseded in the topology that process reads after each heartbeat, so the old process
learns of it within one check of the *server* (``autoscale.period``) after the newer relay
registers. The old process then retires. It asks for no more work and launches no more *clients*,
and it ends none of those it has. It hands back to the upstream *server* every task none of its
*clients* has started, and the *server* puts them back in the queue at once, for the newer relay,
within a few heartbeats rather than at an eviction. Its *clients* finish the tasks they hold and
leave, and their results go back upstream. The old process then ends its registration with a final
heartbeat and exits with status 0. While it retires it says so on every heartbeat, so an upstream
*server* that restarts meanwhile, or that evicts it and sees it register again, retires it again at
once and leaves the site with the newer relay, whichever of the two registers first. A relay of an
earlier release does not read the mark and behaves as before: tasks it pulled after being
superseded wait until it exits and the upstream *server* evicts it.

.. versionchanged:: 2.9.0
   :pr:`101`: a superseded relay hands back the tasks its *clients* have not started, and exits.

Against any other upstream *server* (one not autoscaling with relay sites, or an earlier release)
each zone keeps its own fleet, as before: ``init`` *clients* at start and one launched again while
fewer than ``min`` run, where here ``min`` is at least 1 and each defaults to the size. A relay
configured with only a size behaves exactly as without bounds.

Every launch names its zone, whether or not a topology is published: ``{zone}`` in the launcher or
its arguments is replaced by the zone's name (``default`` for the default zone), and the launched
process has it in ``HYPERSHELL_ZONE``.

What a relay does not do
------------------------

A relay moves work across a wide-area hop to a site the upstream *server* cannot reach directly, and
keeps it running when that hop fails. It is not meant for, and does not provide:

- **More throughput.** A relay adds a hop. Tasks run no faster at its site than they would on
  *clients* connected straight to the upstream *server*; it holds at most about two rounds of work
  per site *client*, so it never takes more than its site can run soon.
- **Cancellation or output retrieval across sites.** ``hs update`` on the upstream *server* does not
  reach a task the relay already holds, and ``hs info --stdout`` / ``--stderr`` reads captured output
  from the host that ran the task, which upstream may have no route to.

A task cut for the relay upstream but not yet confirmed when the relay dies (or when its connection
drops) stays assigned upstream with no *client*; this window exists for every *client*, and is larger
for a relay by its bundle size.
