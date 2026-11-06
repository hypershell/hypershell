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
- **Autoscaling by site topology.** Each served zone's fleet has a fixed size (``-N``, or the zone's
  own ``size``); the relay launches another *client* only when one exits.
- **Tolerance of clock skew.** The upstream *server* ages a relay's heartbeats by the relay's own
  clock, so a relay host whose clock is far off the upstream *server*'s is evicted while alive (and
  its tasks put back). Keep every relay host's clock synchronised with the upstream *server*'s.

A task cut for the relay upstream but not yet confirmed when the relay dies (or when its connection
drops) stays assigned upstream with no *client*; this window exists for every *client*, and is larger
for a relay by its bundle size.
