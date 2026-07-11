# GOAL — {Title}

> **Origin spec.** The *what* and *why* — the locked contract `hs-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `hs-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** {slug}
- **kind:** feature | fix | refactor
- **appetite:** small | big  ·  *small caps research + phase count; a one-sentence change may skip
  the lifecycle entirely.*

## Problem

<The raw need, in plain language. What hurts today, for whom, and why it matters. One or two
paragraphs. Motivate the work — do not describe the solution yet.>

## Outcome / vision

<What "good" looks like when this ships. The shared picture we're agreeing on.>

## Acceptance criteria (the contract)

Stable IDs (`R1`, `R2`, …) that survive squash-merge and anchor traceability. Prefer **EARS**
phrasing (see [`.agents/factory/ears.md`](../../.agents/factory/ears.md)) — it makes each line
directly testable — but plain, unambiguous prose is acceptable where EARS would be forced.

- **R1** — WHEN <trigger>, the <component> SHALL <observable response>.
- **R2** — WHILE <state>, the <component> SHALL <response>.
- **R3** — IF <unwanted condition>, THEN the <component> SHALL <response>.
- **R4** — The <component> SHALL <ubiquitous requirement>.

## Non-goals (no-gos)

Explicit exclusions that keep scope bounded to the appetite. Naming what we are **not** doing is as
important as what we are.

- <thing deliberately out of scope>

## Clarifications

Questions resolved with the human during shaping. Unresolved ones stay marked `[NEEDS
CLARIFICATION: …]` and **block** `hs-plan` — never guess.

- **Q:** <question> — **A:** <answer> (resolved YYYY-MM-DD).

## Related materials

- Issue: <https://github.com/hypershell/hypershell/issues/NN>
- <docs, prior art, source paths, external references>
