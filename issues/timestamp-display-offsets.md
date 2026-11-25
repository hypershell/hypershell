---
status: unshaped
kind: fix
appetite: small
target: 2.9.x
lane: public
---

# `hs info` and `hs list` show a timestamp's offset differently on each backend

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

`hs info` and `hs list` print a stored timestamp one way on PostgreSQL and another on SQLite.
PostgreSQL prints it with an offset, in the server session's time zone (`2026-01-01 09:00:00-05:00`;
under `PGTZ=UTC`, `2026-01-01 14:00:00+00:00`). SQLite prints no offset (`2026-01-01 09:00:00`). The
instant and the local wall time agree; only the display differs. Even in CI, where both zones are
UTC, the two differ by the `+00:00` suffix. Every stamp shows the split today, `submitted:` included.

Found by `update-typed-columns`' plan research (`spec/update-typed-columns/research/03-datetime.md`
§4) and deferred from its plan gate (Q1, 2026-11-24): that cycle leaves the display alone.
**Pre-existing.**

## Outcome sought

One display rule for every timestamp on both backends, in `hs info` and in each `hs list` format.

## Notes

- In scope: the display of every timestamp column, not only the ones `hs update` writes.
- Display only. `to_json_type` is also the wire format and must not change.
- The plan's sketch: a display-only step in `format_json` and in `hs list`'s json and csv paths
  (`spec/update-typed-columns/PLAN.md`, Q1). Shaping picks the rule, such as the local zone with
  its offset, or none.
- Changed output is an interface change, which shaping weighs against the Target.
