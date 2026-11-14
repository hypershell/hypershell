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

## F3 — A planned verify gate failed on a correct build
`origin=hs-build:P3 severity=low category=steering status=open target=.agents/skills/hs-plan/SKILL.md`
- **What happened:** P3's `verify:` ended with `HYPERSHELL_AUTOSCALE_PERIOD=5MB uv run hs list > /dev/null`
  under `&&`. A bare `hs list` prints its usage and exits 1, so the gate was red with the relay fix in
  place; the build amended it to `hs list --count` with `set_phase.py --verify`.
- **Skill cause:** `hs-plan` writes each phase's `verify:` without running its CLI pieces against
  `develop`; a piece whose exit status does not depend on the change can still be wrong on its own.
- **Recommended fix:** In `hs-plan`, run every drive step of a `verify:` that does not depend on the
  new behaviour against `develop` in a `temp_site` before writing it, and note that a post-condition
  must not lean on a command's usage exit.
- **Confidence:** med · **Effort:** small

## F4 — No disposition for a finding one criterion requires and another forbids
`origin=hs-review:step4 severity=low category=missing-guidance status=open target=.agents/factory/review-rubric.md`
- **What happened:** The blind reviewer reproduced `-w 1_000` refused where `develop` read 1000. R5
  forbids that; R4, the Outcome and a Non-goal require it. The reviewer could only grade it
  "MEDIUM, CONFIRMED, needs a gate decision". The rubric's *Verdict & loop* offers three routes:
  CONFIRMED blocks, PLAUSIBLE goes to triage, and pre-existing behaviour is deferred. None fits new
  behaviour that one criterion mandates, so the orchestrator had to improvise a non-blocking
  disposition.
- **Skill cause:** The rubric assumes GOAL criteria never conflict. A CONFIRMED finding whose only
  remedy fails another criterion cannot converge in `hs-build`, and the rubric gives no route to the
  gate for it.
- **Recommended fix:** Add a "contract conflict" class to *Verdict & loop*. The finding is recorded
  with both criteria cited and does not loop to `hs-build`. It needs a gate ruling before publish,
  and if the ruling goes against the branch it becomes CONFIRMED. The human gate must be kept for
  core files.
- **Confidence:** med · **Effort:** small

## F5 — Pre-flight never checks that the branch still merges into a moved base
`origin=hs-review:step1 severity=low category=missing-guidance status=open target=.agents/skills/hs-review/SKILL.md`
- **What happened:** `develop` moved (#105, which touched `server.py` and the server docs) after the
  branch was cut. Only the brief asked for a merge check. `git merge-tree` found a conflict in
  `docs/cli/server.rst`, and the merged tree needed its own test and docs runs.
- **Skill cause:** Step 1 resolves the base and diffs against the merge-base. It never fetches or
  tests the merge, and the injected diffstat reads local `develop`, so a conflict or an interaction
  with the moved base shows up only at publish.
- **Recommended fix:** In Step 1, run `git fetch` and then
  `git merge-tree --write-tree origin/{base} HEAD`. On a conflict or a moved base, record it under
  the Verification run. If the move touched the coupled core, re-run the affected tests on the merged
  tree.
- **Confidence:** med · **Effort:** small
