# META — `auth_env` and `auth_eval` in a config file break every command

> **Harness feedback log** for this feature — the producer artifact of the factory's self-improvement
> loop. Written by the lifecycle skills (`hs-feature` / `hs-plan` / `hs-build` / `hs-review`) when the
> **skillset itself** costs something; read by `hs-publish` (surfaced in the PR) and applied by
> `/hs-harness`. This file is **orthogonal** to the `GOAL → PLAN → TECH → REVIEW` spine — it is about
> the *toolchain*, not the feature — and is retained on merge like the rest of `spec/{slug}/`.
>
> **Silence is the default.** The bar for a finding is one test: *was this the **skill's** fault — not
> mine, not the task's?* The blind `hs-review` correctness reviewer never reads this file.

- **slug:** server-auth-variant-collision

## What worked well

- `hs-plan` Step 3's high-blast-radius exception: pinning every read site of the key before design
  showed the two class-body reads were the import-time failure, which set the lazy-resolution design.

## Friction findings

## F1 — `hs-plan` has no step for a fix that also ships on a patch line
`origin=hs-plan:step-4 severity=low category=missing-guidance status=open target=.agents/skills/hs-plan/SKILL.md`
- **What happened:** the GOAL says the fix is cherry-picked onto `2.8.6`. The drift diff of the
  touched files, the separable 2.9-only part, the expected-conflict drop list and the `git merge-tree`
  gate all came from the cycle brief and the units fix's TECH, not from this skill.
- **Skill cause:** Steps 4–6 never mention a release line other than `develop`, though GOAL
  Clarifications can name one; a planner without that brief would write an unpickable plan.
- **Recommended fix:** add to Step 4: when the GOAL names a patch release, record in PLAN the drift of
  each touched file against the tag, the pick order and drop list, and give the last phase a
  `git merge-tree --merge-base=<plan commit> <tag> "${W:-HEAD}"` gate with `W=$(git stash create)`,
  which reads the working tree before the phase commit and so also closes
  `spec/size-and-duration-units/META.md` F2.
- **Confidence:** med · **Effort:** small
