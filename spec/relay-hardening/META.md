# META — Relay hardening for 2.9.0

> **Harness feedback log** for this feature — the producer artifact of the factory's self-improvement
> loop. Written by the lifecycle skills (`hs-feature` / `hs-plan` / `hs-build` / `hs-review`) when the
> **skillset itself** costs something; read by `hs-publish` (surfaced in the PR) and applied by
> `/hs-harness`. This file is **orthogonal** to the `GOAL → PLAN → TECH → REVIEW` spine — it is about
> the *toolchain*, not the feature — and is retained on merge like the rest of `spec/{slug}/`.

- **slug:** relay-hardening

## Friction findings

<!-- Real findings are appended below this line by the lifecycle skills. -->

## F1 — No guidance for promoting several seeds into one GOAL, or for a sibling seed the GOAL subsumes · seen again
`origin=hs-feature:step4 severity=low category=missing-guidance status=open target=.claude/skills/hs-feature/SKILL.md`
- **What happened:** This cycle promotes three unshaped seeds (`relay-abort-races` primary, `upstream-link-restart-proxy`, `relay-hardening`) under a slug that is not the primary's stem, and its shared eviction-revert fix subsumes a fourth seed (`orphan-revert-loses-result`) the brief did not name. Step 4 and Step 7 cover one seed whose stem is the slug; I followed the `writeback-scoped-conditional` / `relay-retire` precedent for the three and left the fourth as a proposal for the gate.
- **Skill cause:** Same as `spec/relay-retire/META.md` F1: the argument parsing, "Promoting an issue" and Step 7's `git add issues/{slug}.md` assume one seed per GOAL and say nothing about a subsumed sibling.
- **Recommended fix:** As in `spec/relay-retire/META.md` F1: accept several `issues/*.md` (first is primary), let the slug differ from the primary's stem, have Step 7 add every adopted seed, and say whether a subsumed sibling the invocation did not name is adopted now or proposed at the gate.
- **Confidence:** high · **Effort:** small

## F2 — Step 1/3 assume local `develop` in the main checkout; cycles run in worktrees · seen again
`origin=hs-feature:step3 severity=low category=instruction status=open target=.claude/skills/hs-feature/SKILL.md`
- **What happened:** The worktree `.worktrees/relay-hardening` on `fix/relay-hardening` off `origin/develop` was created before this step ran; Step 1's "on `develop`" check and Step 3's `git switch -c` do not apply, and were skipped by the brief's instruction.
- **Skill cause:** The steps and allowed-tools predate the worktree practice every recent cycle uses (open in earlier cycles' META files).
- **Recommended fix:** Make Step 3 a worktree under `.worktrees/{slug}` off `origin/develop`, accept an existing one on the right branch in Step 1, and add `Bash(git worktree *)` to allowed-tools.
- **Confidence:** high · **Effort:** small

## F3 — No rule for an unshaped seed whose claim is already fixed on `develop`, or for meeting part of a neighbouring seed
`origin=hs-feature:step4 severity=low category=missing-guidance status=open target=.claude/skills/hs-feature/SKILL.md`
- **What happened:** One item of `relay-hardening` (the man page synopsis) was already fixed on `develop` by #89, and R2 meets one criterion (R1) of `hypertable-contended-scheduler-and-cli-writes`, which stays `unshaped`. The skill asks to re-check drift only for a `shaped` seed, and has no way to record a partly met neighbour; I recorded the first in Problem and Clarifications and the second as a Non-goal telling `/hs-roadmap` to narrow that seed.
- **Skill cause:** "Promoting an issue" tells an `unshaped` promotion to carry the evidence forward but not to verify each claim against current `develop`, and the status vocabulary has no "partly met by {slug}" marker (related to `spec/relay/META.md` F2).
- **Recommended fix:** For every promotion, verify each seed claim against current `develop` and drop (with the fixing commit cited) any already fixed; and give `/hs-roadmap` a documented step for narrowing a seed whose criterion another cycle met, triggered by a Non-goal line naming it.
- **Confidence:** med · **Effort:** small

## F4 — No safe docs-build verify command; precedents clobber the worktree's `.venv`
`origin=hs-plan:step6 severity=low category=template status=open target=.agents/factory/templates/TECH.md`
- **What happened:** The verify commands of recent cycles (e.g. `spec/relay-retire/TECH.md` P3) build the docs with `uv run --python 3.12 --group docs sphinx-build … docs docs/_build/html`, which re-syncs the project environment to another interpreter, replacing the worktree's `.venv`, and writes into the tree. The safe form (`UV_PROJECT_ENVIRONMENT=$TMPDIR/docs-venv … $TMPDIR/docs-html`) reached this plan only through the foreman's brief.
- **Skill cause:** Neither Step 6 nor the TECH template gives a docs-build `verify:` form, so each plan copies the last cycle's.
- **Recommended fix:** Add the `UV_PROJECT_ENVIRONMENT=$TMPDIR/docs-venv uv run --python 3.12 --group docs sphinx-build -W --keep-going -q -E -b html docs $TMPDIR/docs-html` form to the TECH template's Conventions (with its sandbox note), and tell Step 6 to use it.
- **Confidence:** high · **Effort:** small

## F5 — No convention for proving R9's "fails on the base commit"
`origin=hs-plan:step6 severity=low category=missing-guidance status=open target=.claude/skills/hs-plan/SKILL.md`
- **What happened:** The GOAL requires each regression test to fail on `2ffb5cc` and pass on the head. Nothing in the skill or templates says how a phase proves the first half, so this plan invented `drive/red_check.sh` + `red.txt` (export the base `src` with `git archive`, run the listed tests with it first on `PYTHONPATH`); `relay-retire` invented `DRIVE_BASE_REV` for drives.
- **Skill cause:** Step 6 asks for a real `verify:` per phase but gives no pattern for the red half of a fails-before/passes-after criterion, which `hs-feature` GOALs routinely state.
- **Recommended fix:** Ship a generic `.agents/factory/bin/red_check.sh <base-rev> <list>` and reference it from Step 6 and the TECH template whenever a GOAL's R-IDs say "fails on <rev>".
- **Confidence:** med · **Effort:** small

## F6 — The blind diff's `spec/` exclusion also hides the drives a GOAL grades
`origin=hs-review:step2 severity=low category=instruction status=open target=.claude/skills/hs-review/SKILL.md`
- **What happened:** R9 grades `spec/relay-hardening/drive/red_check.sh`, `red.txt` and the drives, and RV3/RV4 were found in them; the skill's only diff command, `git diff {base}...HEAD -- . ':(exclude)spec/'`, leaves them out. The reviewer saw them only because the brief added `git diff {base}...HEAD -- 'spec/*/drive/*'`.
- **Skill cause:** Step 2 and the rubric treat everything under `spec/` as author rationale, but `spec/{slug}/drive/` holds the evidence tooling a GOAL routinely names as a requirement's proof.
- **Recommended fix:** In Step 2 and the rubric's "What the reviewer sees", add the drive pathspec as a second diff the reviewer gets, while PLAN/TECH/research/META/evidence stay excluded. Blind-review integrity is kept only if drive scripts carry no design rationale; say so in the hs-plan drive guidance.
- **Confidence:** high · **Effort:** small

## F7 — No rule for grading a criterion whose record the GOAL puts in `TECH.md`
`origin=hs-review:step3 severity=low category=missing-guidance status=open target=.claude/skills/hs-review/SKILL.md`
- **What happened:** R8's outcome (b) is met only if `TECH.md` records the runs, tails, logs and causes. The blind reviewer may not read `TECH.md`, and the skill lets the orchestrator read it for `base`/`slug`/`kind` only; I graded the R8 record myself and kept its content out of the reviewer's inputs.
- **Skill cause:** The skill assumes every R-ID is graded from the diff, so a GOAL that names `TECH.md` as its record has no grader.
- **Recommended fix:** Say in Step 3 that the orchestrator grades any criterion whose record the GOAL places in `TECH.md`, by reading only that record, and notes it in REVIEW.md; the reviewer's matrix marks such a row "orchestrator's". Do not pass the record to the reviewer: that would weaken blind review.
- **Confidence:** high · **Effort:** small
