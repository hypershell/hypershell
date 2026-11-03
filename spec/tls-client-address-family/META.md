# META — TLS client reaches the server on the address family it listens on

> **Harness feedback log** for this feature — the producer artifact of the factory's self-improvement
> loop. Written by the lifecycle skills (`hs-feature` / `hs-plan` / `hs-build` / `hs-review`) when the
> **skillset itself** costs something; read by `hs-publish` (surfaced in the PR) and applied by
> `/hs-harness`. This file is **orthogonal** to the `GOAL → PLAN → TECH → REVIEW` spine — it is about
> the *toolchain*, not the feature — and is retained on merge like the rest of `spec/{slug}/`.
>
> **Silence is the default.** The bar for a finding is one test: *was this the **skill's** fault — not
> mine, not the task's?* A merely-hard task, a self-inflicted error, or a one-off content/code issue
> (that belongs in `GOAL.md` / `REVIEW.md`) is **not** a finding. The blind `hs-review` correctness
> reviewer never reads this file — it would leak author intent.

- **slug:** tls-client-address-family

## Friction findings

Zero or more findings, appended below — each a markdown **section** so appending is a low-corruption
operation and a stdlib parser reads them (`uv run python .agents/factory/bin/meta_status.py
spec/{slug}/META.md`). Skills always write `status=open`; only `/hs-harness` flips it. `target` is a
best-guess file with **no line number** (re-derive the exact edit at apply time to avoid staleness). If
an equivalent finding already exists, append "· seen again" to its title instead of duplicating —
recurrence is signal, not bloat.

Field enums — `severity`: `high` (a safety / gate / correctness gap) `| medium | low`; `category`:
`instruction | steering | tooling | template | missing-guidance`; `status`: `open` (written by skills)
`| applied | rejected | deferred` (written by `/hs-harness`).

Schema (copy one block per finding, appending it **after** this fence — the fence is illustrative and
is skipped by the parser):

```markdown
## F1 — <one-line title of the skillset problem>
`origin=<skill>:<step> severity=<high|medium|low> category=<instruction|steering|tooling|template|missing-guidance> status=open target=<best-guess file>`
- **What happened:** <what the skill made you do, or fail to do>.
- **Skill cause:** <why this is the instructions' fault — not yours, not the task's>.
- **Recommended fix:** <the concrete change to the skill / template / script>.
- **Confidence:** <high|med|low> · **Effort:** <small|medium|large>
```

<!-- Real findings are appended below this line by the lifecycle skills. -->

## F1 — Blast-radius research exception has no depth scale for an already-diagnosed fix
`origin=hs-plan:step3 severity=low category=instruction status=open target=.agents/skills/hs-plan/SKILL.md`
- **What happened:** a `kind: fix`, `appetite: small` change to one function in `core/queue.py`, with the
  root cause already diagnosed in the GOAL, tripped Step 3's high-blast-radius exception ("run the full
  fan-out … regardless"). The planner had to decide alone what "full" means here, and settled on two
  sequential briefs plus a digest.
- **Skill cause:** the exception says when to fan out but not how much. "Full fan-out" reads as the
  `appetite: big` procedure (parallel agents, one per rabbit hole), which is out of proportion when the only
  unknowns are the touched contracts. The exception's own rationale is to pin those contracts.
- **Recommended fix:** in Step 3's high-blast-radius exception, scale depth to the unknowns: with a known
  root cause, a fan-out scoped to the touched contracts (one brief per touched high-blast-radius
  file/contract, which may be written inline) satisfies it; the parallel `appetite: big` fan-out is for when
  the mechanism is also unknown. This keeps the trigger itself and does not weaken the gate.
- **Confidence:** med · **Effort:** small
