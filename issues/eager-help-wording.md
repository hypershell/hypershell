---
status: unshaped
kind: docs
appetite: small
target: 2.9.x
lane: public
---

# The `--eager` help says "greater than one" where it means zero

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

The `--eager` help text describes the retry condition as attempts "greater than one"; the behaviour turns on a retry count above zero. Found by `config-set-and-unread`'s plan research.

## Direction

Correct the help text and its docs snippet.

## Related

- `spec/config-set-and-unread/research/`.
