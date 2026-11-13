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

|

.. include:: ../_include/relay_help.rst


.. _cli_relay_topology:

Topology
--------

.. versionadded:: 2.9.0

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
its site starts, and launches what the server asks of each zone beyond what that zone already has,
each with the zone's own launcher. It never ends a *client* to reconcile: a lowered target, or a
target of zero, takes effect as the zone's *clients* exit after ``relay.timeout`` idle seconds. A
*client* that exits is launched again only when the server still asks for it: when a later plan
does, or, while the plan stands unchanged, once the server has planned twice more since the relay's
heartbeat reported the exit (a *client* that leaves before any heartbeat counts it changes nothing
the server sees, so no later plan comes). A server that plans less often than the relay checks is
never taken for one that lost count. With no *clients* left the relay stays connected, ready to
grow. A raised bound lets a later plan assign more; bounds are read
when the relay starts, so changing them means restarting it.

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
- **Tolerance of clock skew.** The upstream *server* ages a relay's heartbeats by the relay's own
  clock, so a relay host whose clock is far off the upstream *server*'s is evicted while alive (and
  its tasks put back). Keep every relay host's clock synchronised with the upstream *server*'s.

A task cut for the relay upstream but not yet confirmed when the relay dies (or when its connection
drops) stays assigned upstream with no *client*; this window exists for every *client*, and is larger
for a relay by its bundle size.
