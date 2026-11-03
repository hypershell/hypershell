---
status: unshaped
kind: refactor
appetite: small
lane: public
---

# Server-owned queue proxies without reference counting, so no finalizer ever dials the server

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

The in-process queue server (zone R1, `spec/zone-queue-primitive/`) serves the four shared queues
to clients over the multiprocessing manager protocol. Each client proxy carries a `util.Finalize`
whose stock callback, `BaseProxy._decref`, opens a connection to the server and waits for a reply.
The cyclic garbage collector can run that finalizer on any thread. When the client lives in the
server's own process, that thread can be one only the server's accept loop could unblock, and the
server stalls. Six review cycles of `zone-queue-primitive` (C5, C7, C8, C9, C10) closed that class
one window at a time:

- address matching (C8, C9);
- a thread rule;
- marks around the TLS handshake;
- a guard for threads still bootstrapping, which was the Python 3.12 gap.

The result is correct on 3.11–3.14 as tested, but it rests on CPython timing and private internals
(`unsupported_internals()`), and a future interpreter could open a new window.

The four queues live as long as the server. A proxy for an object the server owns does not need the
manager's reference count to keep that object alive: the stock proxy supports `manager_owned=True`,
which skips both `incref` and the decref finalizer. If the client's queue proxies were built that
way, no finalizer would ever open a connection, and the whole class would be impossible by
construction instead of closed by rules.

## What to find out before shaping

- **The server side.** How `Server.create`/`incref`/`decref` count references for objects returned
  by the registered callables, and whether a client that never sends `incref`/`decref` leaves
  entries in `id_to_obj`/`id_to_refcount` that grow across client reconnects. Ident is
  `id(obj)`, so the same queue should map to one entry, but this needs checking on 3.11–3.14.
- **The wire.** Remote 2.9 clients would send fewer messages. Clients from `b819de6` and 2.8.x still
  send `incref`/`decref` and must behave exactly as today.
- **The other proxy-returned objects,** if any: the plain `create` path.
- **Retirement.** Whether the thread rule, the TLS-handshake marks, the bootstrap guard, the sender
  thread and their private-internals checks can then be removed, or must stay for remote proxies.

## Acceptance sketch

- A same-process client dropped as cyclic garbage on any thread never opens a connection to the
  server. The `tests/test_queue_gc.py` probes pass without the hand-off machinery.
- Server memory is flat across many client connect/disconnect cycles.
- Compat and parity with `b819de6` clients hold, TLS on and off.

## Notes

- Found by: the foreman's assessment of `zone-queue-primitive`'s review history, 2026-11-03, as a
  simpler design to test after zones ship. It is not a defect in the shipped behaviour.
