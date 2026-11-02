---
status: unshaped
kind: fix
appetite: small
lane: public
---

# A late confirmation can overwrite the executing client's attribution

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

Confirmations and results travel on separate queues, drained by separate server threads (`Confirm`
and `Receiver`), so their order is not guaranteed. A confirmation writes `client_id` and
`client_host` onto its rows by id, unconditionally, even when the row is already complete.

For an ordinary client this is harmless: the confirmation and the result name the same client. It
stops being harmless for **relay** (`issues/relay.md`), which receives rows from upstream, confirms
them under its own identity, and returns each result attributed to the leaf client that actually ran
it. `writeback-scoped-conditional` made that leaf attribution a result column the server accepts
(its R4). If the relay's confirmation is applied *after* the result, it overwrites the leaf
attribution with the relay's own id, and the completed row records the wrong executing client.

Evidence: found by reading during `writeback-scoped-conditional`'s research
(`spec/writeback-scoped-conditional/research/02-callers-concurrency-tests.md` §2). **Not
reproduced**. **Pre-existing** in `develop` (today the Receiver writes `client_id` from the copy
too, so the same overwrite happens).

## Why it was deferred

Out of `writeback-scoped-conditional`'s scope, which fenced results only and held the wire format
fixed. It matters only once relay returns rows with leaf attribution.

## Outcome / vision

Once a row is complete, its executing-client attribution comes from the result, and no
confirmation can rewrite it.

## Sketch of the acceptance criteria

- **R1** — A test SHALL apply a result attributed to client C for a row confirmed to holder H, then
  apply H's confirmation, and show the row's final attribution.
- **R2** — WHEN a confirmation arrives for a row that is already complete, THEN the server SHALL
  leave `client_id` and `client_host` unchanged.
- **R3** — Ordinary clients (confirmation before result, same client) SHALL behave as today.

## Notes

- **Relay-facing.** Carry this to relay's shaping alongside its return-path note.
- A cheap partial fix needs no wire change: condition the confirmation's update on
  `completion_time IS NULL`. The stale-confirmation case in
  `issues/stale-confirm-hides-orphan.md` needs the dispatch token on the wire.
- Found by: `writeback-scoped-conditional` plan research, 2026-11-02.
