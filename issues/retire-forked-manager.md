---
status: unshaped
kind: refactor
appetite: small
target: 2.10.0
lane: public
---

# Retire the forked queue manager

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and the R-IDs get negotiated with a human. The `status:` field above is the
> guard.

## Problem

`SecureManager.start` (`src/hypershell/core/queue.py:1025`) still serves the queue from a forked child
process, bootstrapped by `_child_bootstrap` (`:407`). No command uses it: `QueueServer.start`
(`:1210`) overrides it and serves in-process. Its only caller is
`test_secure_manager_subprocess_roundtrip` (`tests/test_queue_tls.py:541`). It keeps the decref
hand-off and handshake-thread logic carrying a second path that every change to the queue has to
reason about. `SecureManager` is public (`__all__`) and named in `docs/security.rst`, so removing
the method is an interface change.

## Outcome sought

- `SecureManager.start` either refuses with a clear error pointing at `QueueServer`, or is removed
  with its child bootstrap. Shaping decides which.
- The in-process server, the decref hand-off and the handshake threads keep their behaviour and
  their tests.
- `docs/security.rst` and AGENTS.md stop describing a forked server.

## Notes

- Deferred by `queue-wire-codec` (maintainer's GOAL-gate ruling, 2026-11-23): that cycle keeps the
  forked path, which gets the new codec through the shared serializer pair, and leaves the
  retirement here.
- Target 2.10.0, because it changes a public Python API. A deprecation warning could come first in
  a 2.9.x patch if shaping wants one.
