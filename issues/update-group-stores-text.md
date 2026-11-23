---
status: unshaped
kind: fix
appetite: small
target: 2.9.0
lane: public
---

# `hs update group=…` stores a non-integer group

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

`hs update` accepts any value for `group` and stores it, so a text group reaches the scheduler even after submit-time validation (`submit-input-checks`) refuses one. Reproduced by that cycle's shaping on `dfdbaed5`.

## Direction

Apply the submit-time integer rule to `hs update group=…`, refusing with exit 2 before anything is written.

## Related

- `spec/submit-input-checks/GOAL.md` (Non-goals).
