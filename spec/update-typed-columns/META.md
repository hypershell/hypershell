# META — hs update reads every column it writes by the column's kind

> **Harness feedback log** for this feature — the producer artifact of the factory's self-improvement
> loop. Written by the lifecycle skills (`hs-feature` / `hs-plan` / `hs-build` / `hs-review`) when the
> **skillset itself** costs something; read by `hs-publish` (surfaced in the PR) and applied by
> `/hs-harness`. This file is **orthogonal** to the `GOAL → PLAN → TECH → REVIEW` spine — it is about
> the *toolchain*, not the feature — and is retained on merge like the rest of `spec/{slug}/`.
>
> **Silence is the default.** The bar for a finding is one test: *was this the **skill's** fault — not
> mine, not the task's?* The blind `hs-review` correctness reviewer never reads this file.

- **slug:** update-typed-columns

## What worked well

- `hs-plan` Step 3: one brief per rabbit hole, written to `research/NN-*.md` and consumed by summary,
  kept five parallel investigations (including live probes on both backends) out of the planner's
  context.

## Friction findings

## F1 — No guidance for a plan whose dependency has not landed on the base
`origin=hs-plan:6 severity=low category=missing-guidance status=open target=.claude/skills/hs-plan/SKILL.md`
- **What happened:** this cycle builds on a branch still in review. The skill and the `TECH.md`
  template assume `base: develop` already holds everything the design uses, so the planner had to
  invent a P0 (merge `origin/develop` once the dependency lands, with `satisfies: []`), a red-list base
  that is not yet known (recorded as a `# base:` header in `red.txt` and read by the verify), and an
  API-pinning verify.
- **Skill cause:** Step 6 and the template say nothing about a `builds after:` GOAL field, a
  dependency-merge phase, or how a red list names a base commit that does not exist at plan time.
- **Recommended fix:** in Step 6, when the GOAL names `builds after:`, add a P0 that merges the base
  once the dependency lands and whose verify imports the dependency API the PLAN names; allow
  `satisfies: []` for it; have red lists carry their base in a `# base:` header read by the verify.
- **Confidence:** med · **Effort:** small
