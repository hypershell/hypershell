---
status: unshaped
kind: test
appetite: small
target: 2.9.x
lane: public
---

# The queue-server hand-back test fails once with "handle is closed"

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

`tests/test_topology_server.py::test_handback_over_a_queue_server_requeues_only_the_relays_assignments`
failed once on CI (Python 3.11, run 45744040150, PR #127, 2026-11-26). The relay's first work request,
`work(relay.dispatcher, RELAY, 7)` (`tests/test_topology_server.py:575`), went through the proxy's
`_callmethod` to `SecureConnection.send` (`src/hypershell/core/queue.py:163`) on a connection whose
handle was already closed:

```
src/hypershell/core/queue.py:1439: in method
    return self._callmethod(name, args, kwds)
multiprocessing/managers.py:821: in _callmethod
    conn.send((self._id, methodname, args, kwds))
src/hypershell/core/queue.py:163: in send
    self._check_closed()
E   OSError: handle is closed
```

The rest of that run passed (5,663 passed, 422 skipped); the same commit passed on Python 3.12, 3.13
and 3.14 and locally, and the change under test touched no queue or topology code. The test sets up
a `QueueServer` on loopback, a `Scheduler` that packs a bundle, then a `QueueClient` that connects and
asks at once.

## Outcome sought

The test passes reliably, or a real defect in how a client's proxy connection can be closed between
`connect()` and its first request is found and fixed.

## Notes

- Not yet reproduced. Start by looping the test (`pytest --count` or a shell loop) on Python 3.11.
- Candidate mechanisms to check: the proxy's per-thread connection being closed by a refusal path
  (the codec's vocabulary checks, `refusal`), a server-side close after the handshake, or a timing
  window in `QueueClient.connect`.
- Related: `queue-wire-codec` (#123) rewrote the connection's framing.
