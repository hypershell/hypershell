---
status: unshaped
kind: docs
appetite: small
lane: public
---

# Docs and runtime surfacing: silent modes, porting traps, stated stances

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

A batch of gaps where the *behaviour* is correct or deliberate but nothing tells the user. Cheap,
and each one currently costs somebody a debugging session.

- **`--no-confirm` silently disables orphan recovery (M7).** Skipped under `no_confirm`
  (`server.py:632`) — documented in `AGENTS.md`, but nothing warns at runtime and nothing surfaces
  permanently stuck in-flight rows. Wanted: a startup warning, and a way to see
  scheduled-but-silent tasks older than the evict window.
- **Captured output is stranded on client hosts (M11, docs half).** Capture paths live under the
  *client's* site dir (`client.py:452-455`); retrieval SSHes back to `task.client_host`
  (`task.py:238`, `:263-266`), so a scaled-away node takes its outputs with it — with no warning at
  capture time. The shared-filesystem assumption deserves prominent documentation now; the real fix
  is `turso`'s S3 capture (R13–R15). *(The host-key half of M11 went to the security lane.)*
- **Replacement tokens collide with GNU Parallel's (L5).** Our `{.}` is *dirname*
  (`core/template.py:132`); Parallel's `{.}` strips the extension. A porting user gets a silently
  wrong argument. Wanted: a migration table in the docs — and a decision, **before v3 freezes the
  names**, on whether `{..}`/`{-}`/`{+}` are final.
- **No input combinators — by design, but unstated (L7).** No `:::`/`::::`, `--colsep`, `-0`,
  `--shuf`, `--tty`. A fine stance; `docs/landscape.rst` should own it explicitly so it reads as
  intent rather than omission.
- **Cross-host wall-clock timing (L10).** Task timing is stamped with the client's clock
  (`client.py:769-773`) and aggregated server-side across hosts (`data/model.py:757-803`); skew
  corrupts durations and rates. At minimum, note the assumption.

## Why it was deferred

Surfaced by the out-of-cycle GNU Parallel comparison (`.local/IMPROVEMENTS.md`: M7, M11, L5, L7,
L10) — a review pass. All **pre-existing** in `develop`.

## Outcome / vision

The modes that quietly change safety properties say so at runtime; the assumptions that only hold on
a shared filesystem or a synchronised clock are written down; the deliberate omissions read as
decisions; and a user porting from GNU Parallel finds the trap before it bites.

## Sketch of the acceptance criteria

- **R1** — WHEN a run starts with orphan recovery disabled, the operator SHALL be warned, and
  long-silent in-flight tasks SHALL be discoverable.
- **R2** — The shared-filesystem assumption for captured output SHALL be documented prominently.
- **R3** — The docs SHALL carry a GNU Parallel token-migration table naming the `{.}` divergence.
- **R4** — `docs/landscape.rst` SHALL state the no-combinators stance explicitly.
- **R5** — The cross-host clock assumption in timing aggregates SHALL be documented.

## Notes

- Verify at shaping time: **L4 (`TASK_SLOT` export) is probably already done.** `spec/task-slot/`
  exists and `zone` cites TASK_SLOT's dual `{}`-substitution/env exposure as established
  precedent — the IMPROVEMENTS review predates it. If it landed, drop L4 rather than re-fixing it.
- R3 has a deadline the others do not: renaming replacement tokens is a v3 breaking-change decision.
  Worth flagging to the maintainer separately if v3 planning starts before this issue is picked up.
- Per repo convention any CLI/help change here updates `docs/_include/*.rst` and `share/` completions
  in the same commit.
- Found by: GNU Parallel comparison review, 2026-08.
