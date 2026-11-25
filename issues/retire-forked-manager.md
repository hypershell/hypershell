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
- `SecureManager` used directly as a client builds each proxy only from the methods its caller
  registers, as HyperShell's own `QueueClient` has since 2.9.0a10 (#123), and never from a list the
  server sends. Today a caller using `SecureManager` directly has to pass an explicit proxy type and
  method list to `register`, as the 2.9.0a10 release notes say. The default should do that itself.

## Notes

- Deferred by `queue-wire-codec` (maintainer's GOAL-gate ruling, 2026-11-23): that cycle keeps the
  forked path, which gets the new codec through the shared serializer pair, and leaves the
  retirement here.
- Also deferred by `queue-wire-codec` (2026-11-25, at the 2.9.0a10 release): the client-side default
  above. #123 made HyperShell's own client build its proxies from its own method lists, but left
  `SecureManager.register`'s generic default as it was.
- Target 2.10.0, because it changes a public Python API. A deprecation warning could come first in
  a 2.9.x patch if shaping wants one.
