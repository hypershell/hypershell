# META — Promote `part` from a bookkeeping tag to a first-class column

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

- **slug:** part-tag-to-column

## Friction findings

## F1 — Step 7 commit-category rule (`else feature`) contradicts practice and omits `refactor`
`origin=hs-feature:step7 severity=low category=instruction status=open target=.agents/skills/hs-feature/SKILL.md`
- **What happened:** Step 7 says `{category} = fix for kind:fix, else feature`, so a `kind: refactor`
  goal would be committed as `[feature] Shape …`. I used `[refactor]` instead, matching AGENTS.md's
  category list and the repo's demonstrated convention (git log: `[docs] Shape docs-trim-migrated-sections
  goal`, i.e. `[{kind}]`).
- **Skill cause:** the skill's `kind → commit-category` mapping is narrower than AGENTS.md (which blesses
  `refactor`/`docs`/… and says "coin a category when it fits") and than observed shape commits, which use
  `[{kind}]`. Same root cause as the docs-trim feature's F1 (kind-taxonomy narrowness at step2) — **seen
  again**, now at step7's commit line.
- **Recommended fix:** make Step 7 emit `[{kind}]` (fix|feature|refactor|docs|…), or explicitly map
  `refactor → refactor`, rather than collapsing everything non-`fix` to `feature`.
- **Confidence:** high · **Effort:** small
