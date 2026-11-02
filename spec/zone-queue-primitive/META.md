# META — Serve the managed queues in-process on a threading primitive (zone, cycle 1)

> **Harness feedback log** for this feature — the producer artifact of the factory's self-improvement
> loop. Written by the lifecycle skills (`hs-feature` / `hs-plan` / `hs-build` / `hs-review`) when the
> **skillset itself** costs something; read by `hs-publish` (surfaced in the PR) and applied by
> `/hs-harness`. This file is **orthogonal** to the `GOAL → PLAN → TECH → REVIEW` spine — it is about
> the *toolchain*, not the feature — and is retained on merge like the rest of `spec/zone-queue-primitive/`.
>
> **Silence is the default.** The bar for a finding is one test: *was this the **skill's** fault — not
> mine, not the task's?* A merely-hard task, a self-inflicted error, or a one-off content/code issue
> (that belongs in `GOAL.md` / `REVIEW.md`) is **not** a finding. The blind `hs-review` correctness
> reviewer never reads this file — it would leak author intent.

- **slug:** zone-queue-primitive

## What worked well

- `hs-feature` Step 4, the `shaped` promotion rule ("re-confirm against current `develop`, cite
  drift"): it gave the verified correction to the seed's Problem 2 a clear home without reopening
  the seed's settled clarifications.

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

## F1 — Step 3 branches from local `develop`, which may be stale · seen again
`origin=hs-feature:step3 severity=medium category=instruction status=open target=.agents/skills/hs-feature/SKILL.md`
- **What happened:** A recurrence of `spec/group-advance-retry-gate/META.md` F1. The cycle was
  opened in a detached worktree at `origin/develop` and branched with
  `git switch --no-track -c feature/zone-queue-primitive origin/develop`, the known workaround,
  instead of Step 3's `git switch -c {branch} develop`.
- **Skill cause:** Unchanged from the original finding: Step 3 branches from the local ref, and
  Step 1's "on `develop`" check cannot pass in a worktree.
- **Recommended fix:** As in the original finding.
- **Confidence:** high · **Effort:** small

## F2 — No way to promote part of a shaped seed and keep the rest alive as `shaped`
`origin=hs-feature:step4 severity=medium category=missing-guidance status=open target=.agents/skills/hs-feature/SKILL.md`
- **What happened:** The `zone` seed is delivered in three cycles, and this one carries only its
  R1. Step 4 offers only whole-seed promotion (`status: adopted:{slug}`). If the seed is marked
  that way, `/hs-roadmap` treats it as shipped once this cycle lands: its Step 4 either deletes the
  seed or rewrites it down to the remainder and resets it to `unshaped`. Either outcome throws away
  the shaping already agreed for R2–R17. The seed was therefore left `status: shaped`, with a
  carve note in its header, an annotation on R1, and a matching ROADMAP refs line. Nothing in the
  harness reads those markers.
- **Skill cause:** Step 4's status vocabulary and the size circuit-breaker's "pilot + follow-ups"
  split cover only the pilot. The follow-ups go into the GOAL's Non-goals, and `/hs-roadmap`
  re-shapes them as `unshaped` work. Nothing covers a seed already shaped as a multi-cycle program.
  This is a sibling of the cross-seed carve found in `spec/schema-upgrade-step/META.md` F2, where
  one seed's R1 was discharged by a different cycle.
- **Recommended fix:** Add a partial-promotion rule to Step 4. The seed keeps its status; it gains
  a machine-readable `carved: {R-IDs} -> {slug}` frontmatter list (or per-R-ID annotations); and
  the receiving GOAL names the R-IDs it carries. Teach `/hs-roadmap` to retire only the carved
  R-IDs when that cycle lands, and to keep a `shaped` seed `shaped`. Fold in `schema-upgrade-step`
  F2 when this is fixed.
- **Confidence:** high · **Effort:** small
