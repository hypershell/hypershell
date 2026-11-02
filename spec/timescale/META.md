# META — TimescaleDB provider: hypertables out of the box (TS1)

> **Harness feedback log** for this feature — the producer artifact of the factory's self-improvement
> loop. Written by the lifecycle skills (`hs-feature` / `hs-plan` / `hs-build` / `hs-review`) when the
> **skillset itself** costs something; read by `hs-publish` (surfaced in the PR) and applied by
> `/hs-harness`. This file is **orthogonal** to the `GOAL → PLAN → TECH → REVIEW` spine — it is about
> the *toolchain*, not the feature — and is retained on merge like the rest of `spec/timescale/`.
>
> **Silence is the default.** The bar for a finding is one test: *was this the **skill's** fault — not
> mine, not the task's?* A merely-hard task, a self-inflicted error, or a one-off content/code issue
> (that belongs in `GOAL.md` / `REVIEW.md`) is **not** a finding. The blind `hs-review` correctness
> reviewer never reads this file — it would leak author intent.

- **slug:** timescale

## What worked well

Brief, optional reinforcement: a part of a skill / the harness that materially helped, so `/hs-harness`
knows what **not** to change. One line each, naming the skill/step. Skip the section entirely if
nothing stands out.

- <what helped, and in which skill/step>

## Friction findings

Zero or more findings, appended below — each a markdown **section** so appending is a low-corruption
operation and a stdlib parser reads them (`uv run python .agents/factory/bin/meta_status.py
spec/timescale/META.md`). Skills always write `status=open`; only `/hs-harness` flips it. `target` is a
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

## F1 — Step 3 branches from local `develop`, which may be stale · seen again
`origin=hs-feature:step3 severity=medium category=instruction status=open target=.agents/skills/hs-feature/SKILL.md`
- **What happened:** A recurrence of `spec/group-advance-retry-gate/META.md` F1 (and
  `spec/zone-queue-primitive/META.md` F1). Local `develop` was stale, so the cycle was opened with
  `git worktree add -b feature/timescale .worktrees/timescale origin/develop` instead of Step 3's
  `git switch -c {branch} develop`, and Step 1's "on `develop`" check could not apply.
- **Skill cause:** Unchanged from the original finding.
- **Recommended fix:** As in the original finding.
- **Confidence:** high · **Effort:** small

## F2 — No rule for R-ID numbering when a seed's R-IDs are partly discharged elsewhere
`origin=hs-feature:step4 severity=low category=missing-guidance status=open target=.agents/skills/hs-feature/SKILL.md`
- **What happened:** The `timescale` seed's R1 was discharged by another cycle
  (`schema-upgrade-step`), and this cycle promotes the remaining R2–R10. Step 4 says nothing about
  whether the GOAL renumbers from R1 or keeps the seed's numbers. The seed's numbers were kept, so
  the seed, the ROADMAP and the GOAL agree, and the GOAL deliberately has no R1. Whole-seed
  promotion (`adopted:{slug}`) was used, since nothing in the seed remains unassigned.
- **Skill cause:** A sibling of `spec/zone-queue-primitive/META.md` F2 and
  `spec/schema-upgrade-step/META.md` F2: Step 4 models promotion as whole-seed only, so a seed
  whose R-IDs are split across cycles has no stated numbering or status rule.
- **Recommended fix:** When the partial-promotion rule from those findings is written, add one
  line: a GOAL keeps the seed's R-ID numbers and names the ones discharged elsewhere; a seed whose
  remaining R-IDs all go to one cycle is promoted whole (`adopted:{slug}`).
- **Confidence:** med · **Effort:** small
