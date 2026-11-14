# META — Every duration option takes the forms the timeouts take

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

- **slug:** duration-units-remaining

## What worked well

- `hs-feature` Step 4, "Promoting an issue": treating the `unshaped` seed's list as input, not the
  contract, is what surfaced the two options it missed (`-P` on `hs server`, `-Q`).

## Friction findings

## F1 — Pre-created worktree branch bypasses Step 1 and Step 3 · seen again
`origin=hs-feature:step1 severity=low category=instruction status=open target=.agents/skills/hs-feature/SKILL.md`
- **What happened:** The foreman pre-created `fix/duration-units-remaining` from `origin/develop` in a
  worktree under `.worktrees/`; Step 1's "on `develop`" check and Step 3's `git switch -c {branch}
  develop` had to be set aside by the brief.
- **Skill cause:** The same finding as `spec/key-option-refused/META.md` F1 (and the stale-`develop`
  findings in `spec/hypertable-update-retry/` and `spec/schema-upgrade-step/`), still open.
- **Recommended fix:** As recorded there: accept a pre-created `{branch}` at the head of
  `origin/develop` in a worktree as a valid Step 1/3 state, and skip the switch.
- **Confidence:** high · **Effort:** small

## F2 — No form for a clarification answered by the shaper but not yet signed off
`origin=hs-feature:step4 severity=low category=missing-guidance status=open target=.agents/skills/hs-feature/SKILL.md`
- **What happened:** Run unattended under a foreman, with no human to ask, the brief told the shaper
  to *decide* how `--delay-start`'s negative form is kept. The skill offers only two states: a
  resolved `**A:**` dated answer from a human, or a blocking `[NEEDS CLARIFICATION]` marker. Five
  decisions were written as `**A (proposed):**` under a heading that says they await sign-off.
- **Skill cause:** Step 4 and the Safety Principles assume `AskUserQuestion` reaches the maintainer;
  they give no shape for a proposed answer awaiting a relayed sign-off, so each foreman-run GOAL
  invents one.
- **Recommended fix:** Add a third Clarifications form, `**A (proposed, YYYY-MM-DD, pending
  sign-off):**`, which blocks `/hs-plan` like a marker until the foreman records the ruling, and list
  such answers in the Step 8 report.
- **Confidence:** med · **Effort:** small
