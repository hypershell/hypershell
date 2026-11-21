# META — Submitted input is checked: `#` is part of the command, a group is an integer, and a group below the reached one is refused

> **Harness feedback log** for this feature — the producer artifact of the factory's self-improvement
> loop. Written by the lifecycle skills (`hs-feature` / `hs-plan` / `hs-build` / `hs-review`) when the
> **skillset itself** costs something; read by `hs-publish` (surfaced in the PR) and applied by
> `/hs-harness`. This file is **orthogonal** to the `GOAL → PLAN → TECH → REVIEW` spine — it is about
> the *toolchain*, not the feature — and is retained on merge like the rest of `spec/submit-input-checks/`.
>
> **Silence is the default.** The bar for a finding is one test: *was this the **skill's** fault — not
> mine, not the task's?* A merely-hard task, a self-inflicted error, or a one-off content/code issue
> (that belongs in `GOAL.md` / `REVIEW.md`) is **not** a finding. The blind `hs-review` correctness
> reviewer never reads this file — it would leak author intent.

- **slug:** submit-input-checks

## Friction findings

<!-- Real findings are appended below this line by the lifecycle skills. -->

## F1 — No guidance for promoting several seeds into one GOAL under a new slug · seen again
`origin=hs-feature:step4 severity=low category=missing-guidance status=open target=.claude/skills/hs-feature/SKILL.md`
- **What happened:** Three seeds promoted under `submit-input-checks`, a slug that is none of their stems. The seeds disagreed on `kind` (two `fix`, one `feature`) and on `status` (two `unshaped`, one `shaped`), so Argument Parsing's "frontmatter supplies `kind`" and Step 4's per-status rules gave no single answer; the invocation set `kind: fix`. One seed's ROADMAP line had no `· *status: …*` marker; one was added, as `spec/task-input-fidelity/` did. The template's single `Seed:` line became a `Seeds:` list by hand.
- **Skill cause:** Same as `spec/task-input-fidelity/META.md` F1: the promotion steps assume one seed whose stem is the slug.
- **Recommended fix:** As there; also say which seed's `kind` wins (or that the invocation must set it) and how a `shaped` seed's resolved clarifications are carried beside an `unshaped` seed's open ones.
- **Confidence:** high · **Effort:** small

## F2 — Step 1/3 assume local `develop` in the main checkout; cycles run in worktrees · seen again
`origin=hs-feature:step3 severity=low category=instruction status=open target=.claude/skills/hs-feature/SKILL.md`
- **What happened:** The foreman created the worktree and `fix/submit-input-checks` off `origin/develop` before this step; Step 1's "on `develop`" check and Step 3's `git switch -c` did not apply and were skipped on the invocation's instruction.
- **Skill cause:** Same as `spec/cluster-launch-failures/META.md` F2: the steps predate the worktree practice.
- **Recommended fix:** As there.
- **Confidence:** high · **Effort:** small
