---
status: unshaped
kind: fix
appetite: small
lane: public
---

# A role's first upstream call after a server restart can fail as a remote error

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

When the upstream server restarts at the same address, a relay's link roles are meant to see the
lost connection as `LinkDown` and reconnect. The first call a role makes on a new worker thread,
through a client created before the restart, can instead fail with `RemoteError` wrapping a
`KeyError`: `multiprocessing` proxies share one connection per thread and address, so the new
thread dials the restarted server with a proxy id the old server minted, and the new server's
`serve_client` does not know it. `UpstreamLink.call` (`src/hypershell/core/reconnect.py`) treats it
as a non-transport error and raises it.

- In the puller (`dispatcher.request`) it ends the relay with exit status 5.
- In the `handback` role (relay-retire, #101) it is caught: the hand-back is refused and the tasks
  are left to eviction, although the restarted server has already requeued them at start.

Found by relay-retire's review cycle 2 (`spec/relay-retire/REVIEW.md`, observation O1). It
reproduces every time with a small harness: an `UpstreamLink` on a `QueueServer`, the `heartbeat`
role calling `client.dispatcher`, the server stopped and started again on its port, then the first
call of another role. It reproduces on `develop` before #101 (role `puller`) and after (role
`handback`). The window in a whole relay is narrow: a single relay survived four upstream restarts
in each of three runs.

## Direction

Treat a stale-proxy `RemoteError` on a role's first call after a reconnect as a transport loss
(reconnect and retry), or re-create the role's proxies when the link reconnects, so no role can see
the previous server's proxy ids. Pin it with the harness above as a regression test.

## Related

- `spec/relay-retire/REVIEW.md` (O1); `spec/relay/` (#82), which introduced the link roles.
- `issues/relay-hardening.md`, `issues/relay-abort-races.md` (neighbouring relay seeds).
