---
status: unshaped
kind: fix
appetite: small
lane: public
---

# 2.8 and 2.9 clients and servers cannot work together, and fail unclearly

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

Task bundles are JSON objects carrying every `Task` column, and the receiving side builds a `Task`
from them by keyword. The 2.9 line added three columns (`source`, `fingerprint`, `part`) that a
2.8.3 `Task` does not accept, so a 2.8.3 peer cannot read a 2.9 bundle. Nothing on the wire names a
version, so neither side can tell the user what is wrong.

- **A 2.8.3 client against a 2.9 server** exits on its first bundle with
  `TypeError: 'source' is an invalid keyword argument for Task`.
- **A 2.9 client against a 2.8.3 server** runs its tasks, but the server cannot read the returned
  results (the same `TypeError`), records none of them, and the run hangs rather than exiting.

Every 2.9 pre-release is affected, from 2.9.0a1 onward. Users who upgrade the server and the
clients at different times, or who keep a client environment from an earlier release, meet a crash
or a hang instead of a version error.

Evidence: cross-version drives during `writeback-scoped-conditional`'s review (cycle 1,
`spec/writeback-scoped-conditional/REVIEW.md`, Observations and the R9 table). **Pre-existing**:
identical on that cycle's base, `d996579`, so that cycle neither causes nor worsens it.

### Reproduction

Both directions use one isolated site (`HYPERSHELL_SITE`, `HYPERSHELL_DATABASE_FILE` and an empty
`HYPERSHELL_CONFIG_FILE`), a SQLite database, loopback, 10 tasks of which 2 always fail, `-r 2` with
a failures file, and were run with TLS on and with TLS off; the outcome does not depend on TLS.
Install release 2.8.3 in its own environment beside a 2.9 checkout.

1. **Old client, new server:** start `hs server` from the 2.9 checkout with the task file, then
   `hs client` from the 2.8.3 environment against it. The client exits on its first bundle with the
   `TypeError` above.
2. **New client, old server:** start `hs server` from the 2.8.3 environment with the task file, then
   `hs client` from the 2.9 checkout against it. The server logs the same `TypeError` when the
   first results come back, records no result, and the run does not finish.

Control: 2.8.3 against 2.8.3 completes normally.

## Why it was deferred

Found by review as an observation, outside `writeback-scoped-conditional`, which changed neither the
wire nor the bundle codec. The clean failure it needs is already part of a shaped cycle (below).

## Outcome / vision

A mismatched client and server fail promptly, in both directions, with one clear error naming the
version mismatch. Nothing crashes with a `TypeError` and no run hangs. The 2.9.0 release notes say
that 2.8 and 2.9 clients and servers do not interoperate.

## Sketch of the acceptance criteria

- **R1** — A scripted drive SHALL pair a 2.8.3 peer with a 2.9 peer in each direction and record
  each side's exit status, its last error, and the time to exit.
- **R2** — WHEN a 2.8.x client connects to a 2.9 server, THEN the server SHALL disconnect it with a
  log line naming the version mismatch and keep serving other clients, and the client SHALL exit
  within a bounded time.
- **R3** — WHEN a 2.9 client connects to a 2.8.x server, THEN the client SHALL exit non-zero within
  a bounded time with one clear error naming the version mismatch.
- **R4** — The 2.9.0 release notes SHALL state the incompatibility and the upgrade order.

## Notes

- `zone-dispatch` is the intended fix. Its version handshake (its R5, and the 2026-11-02 ruling that
  a new client against an older server exits non-zero with one clear error naming the mismatch) is
  this clean failure. It SHALL be verified against 2.8.3 there, in both directions, rather than
  built twice. This seed stays open until that verification is on record.
- Related: `issues/zone.md`, `issues/date-like-args-coerced.md` (the same bundle codec).
- Found by: `writeback-scoped-conditional` review, cycle 1, 2026-11-02.
