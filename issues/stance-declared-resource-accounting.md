---
status: declined
kind: feature
appetite: big
lane: public
---

# Stance: resource gating stays declared, not measured

> **Stance record, not a defect.** Recorded so a future comparison review does not re-raise it as an
> omission. Not queued for a cycle.

## The observation

Two related GNU Parallel capabilities we do not have:

- **Measured resource gating (M1).** Our totals are sampled once at import
  (`core/resource.py:29-30`) and gating is arithmetic over *declared* task requirements
  (`client.py:531-567`); default per-task `memory` is `0` = unconstrained (`core/config.py:118`), so
  undeclared workloads are never gated and can OOM or swap-thrash a shared node. `--monitor` only
  warns after the fact (`client.py:797-811`). No `getloadavg`/meminfo/swap measurement exists
  anywhere. Parallel gates job start on *measured* free memory (`parallel:4779-4785`), load
  (`parallel:4767`, `:9039`), and swap, and recovers under pressure by killing the youngest job and
  requeueing it (`parallel:7876`, `:8000-8019`).
- **Adaptive straggler timeout (L1).** Walltime is a static per-task/client value
  (`client.py:823`); Parallel's `--timeout 200%` kills jobs relative to a running median of
  successful runtimes (`parallel:16187-16215`).

## The stance

**Declined as debt** (2026-08-07). Both are coherent features *for Parallel's model* — one machine,
one coordinator, jobs it can measure and re-queue locally. HyperShell's model is a fleet of clients
under a batch scheduler, where the node's resources are already allotted by Slurm/PBS and the
declared-accounting scheme (with priority aging and backfill) is the deliberate contract. Measuring
free memory on a node whose allocation is enforced externally invites a second, disagreeing opinion
about what is available.

Killing and requeueing the youngest job under memory pressure — Parallel's recovery move — is
actively wrong here: our retry model is new rows with a UNIQUE chain, and pressure-driven kills
would burn attempts on tasks that did nothing wrong.

Neither is refused forever. Optional, explicitly opt-in gates (`min_memfree`, `max_load` in
`acquire_resources`) and an adaptive timeout are both defensible as *additive* knobs if a user asks.
They are declined as **standing debt** — we are not carrying them as things HyperShell ought to have.

## What would change the answer

- A user reporting real node-level OOM/thrash under declared accounting that better *declaration*
  ergonomics could not fix.
- Long campaigns where rare hangs, not failures, are the dominant loss — the adaptive-timeout case.

## Notes

- The related-but-different question of *routing* a task to a node that fits it is answered by
  `zone`, not by measurement — see `issues/resource-error-terminal-and-retry-defaults.md`.
- Origin: GNU Parallel comparison review, 2026-08 (M1, L1); declined 2026-08-07.
