---
status: unshaped
kind: test
appetite: small
lane: public
---

# `pytest -n auto` fails at collection: parametrized ids differ between workers

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

`AGENTS.md` § Commands recommends `pytest -n auto` for a fast local run, but on `develop`
(`90ab2fc2`, 2026-11-15) it fails during collection: pytest-xdist requires every worker to collect
the same test ids, and some parametrized tests build their ids from values that differ per process
(found by the plan of `cluster-nodb-and-zero-period`; not yet narrowed to the tests responsible).
Every cycle now runs the suite serially, about 25 minutes.

## Direction

Find the parametrizations whose ids are not deterministic (values from a random source, a port, a
temporary path, a set's order or an object `repr`), give them stable `ids=`, and confirm that
`pytest -n auto` collects and passes. If some tests cannot run in parallel, mark them so rather
than dropping the recommendation; otherwise correct `AGENTS.md`.

## Related

- Found by: `cluster-nodb-and-zero-period` planning, 2026-11-15.
