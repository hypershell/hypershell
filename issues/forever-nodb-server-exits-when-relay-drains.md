---
status: unshaped
kind: fix
appetite: small
target: 2.9.x
lane: public
---

# `hs server --forever --no-db` exits once its only relay drains

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

Found by the plan of `relay-advertise` (2026-11-21), on `develop` (`08d867df`), not yet narrowed to
a cause: an upstream `hs server --forever --no-db` with one `hs relay` exits 0 as soon as that relay
has drained, instead of staying up for more work as `--forever` says. The planner's reading is that
the in-memory server has no scheduler state for `--forever` to keep alive. The same run against a
server with a database stays up. Without `--forever`, exiting once the work is done is correct.

## Direction

Reproduce with a plain client as well as a relay, to see whether it is relay-specific. Make a
`--forever --no-db` server keep serving until it is signalled, as the database form does, and pin it
with a bounded test.

## Related

- `spec/relay-advertise/PLAN.md` (plan-gate rulings, "Out of scope").
