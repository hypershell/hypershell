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

## F2 — `hs-review` grades against local `develop`, which can be stale · seen again (review cycle 2)
`origin=hs-review:step1 severity=medium category=instruction status=open target=.agents/skills/hs-review/SKILL.md`
- **What happened:** the injected diffstat and the Step 1 base both name `develop`; in this worktree local `develop` (222075b) lagged `origin/develop` (15587cf), so a literal run would have graded unrelated upstream commits as part of this branch's diff. The reviewer had to be told by hand to use `origin/develop`.
- **Skill cause:** the skill never says to fetch or to compare against the remote-tracking base, and `TECH.md`'s `base: develop` is read verbatim.
- **Recommended fix:** resolve `{base}` to `origin/{base}` after a `git fetch` (or check `git merge-base --is-ancestor {base} origin/{base}` and warn) in the injected diffstat, Step 1 and Step 2's diff command.
- **Confidence:** high · **Effort:** small

## F3 — the patch-line pick gate proves the pick applies, not that its suite passes
`origin=hs-build:P2 severity=medium category=steering status=open target=.agents/skills/hs-plan/SKILL.md`
- **What happened:** P2's `git merge-tree` gate against `2.8.6` was green, yet five new tests failed
  on the real pick (a shutdown timing difference on `2.8.6`); only review cycle 1 found it, by
  running the suite in a scratch clone. Once P3 landed, P2's own gate also went red at `HEAD`,
  since it predates the relay paths; the remediation had to widen it.
- **Skill cause:** the gate F1 recommends checks conflicts only; nothing asks a build to run the
  suite on the picked tree, and nothing says an earlier phase's tree-wide gate must still hold
  after a later phase lands.
- **Recommended fix:** for a patch-line fix, give the last pickable phase a behavioural gate (a
  scratch clone, the base commit then every non-2.9 commit cherry-picked onto `<tag>^{commit}`, the
  agreed drop list, the full suite on the line's Python and pins), and write each phase's
  merge-tree exclusions as the union over all phases.
- **Confidence:** med · **Effort:** medium
