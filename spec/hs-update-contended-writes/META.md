# META — `hs update` contended writes

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

- **slug:** hs-update-contended-writes

## What worked well

- `hs-feature` Step 4's `unshaped` rule ("treat the draft R-IDs as input, not as the contract") kept
  the seed's sketch from being copied: re-reading the code turned up the PostgreSQL multi-key
  variant of the seed's second defect and the newcomer-row question, both now sign-off items.

## Friction findings

<!-- Real findings are appended below this line by the lifecycle skills. -->

## F1 — Pre-created worktree branch bypasses Step 1 and Step 3 · seen again
`origin=hs-feature:step1 severity=low category=instruction status=open target=.claude/skills/hs-feature/SKILL.md`
- **What happened:** The foreman pre-created `fix/hs-update-contended-writes` off `origin/develop`
  in a worktree under `.worktrees/`; Step 1's "on `develop`" check, Step 2's branch-collision STOP
  and Step 3's `git switch -c {branch} develop` all had to be set aside by the brief.
- **Skill cause:** The same finding as `spec/duration-units-remaining/META.md` F1 and
  `spec/key-option-refused/META.md` F1, still open.
- **Recommended fix:** As recorded there: accept an existing `{branch}` checked out in a worktree at
  `origin/develop` with a clean tree as passing Steps 1–3.
- **Confidence:** high · **Effort:** small

## F2 — No form for a clarification answered by the shaper but not yet signed off · seen again
`origin=hs-feature:step4 severity=low category=missing-guidance status=open target=.claude/skills/hs-feature/SKILL.md`
- **What happened:** Run under a foreman with no human to ask, five questions the brief did not
  settle (newcomer rows, the warning's level, the PostgreSQL multi-key variant, uncontended cost,
  `--limit`'s single transaction) were written as `**A (proposed):**` under a heading that says they
  await sign-off, rather than as blocking `[NEEDS CLARIFICATION]` markers.
- **Skill cause:** The same finding as `spec/duration-units-remaining/META.md` F2, still open: the
  skill offers only a human-dated answer or a blocking marker.
- **Recommended fix:** As recorded there: name a third state, a proposed answer awaiting the gate's
  sign-off, which blocks `hs-plan` until the gate rules on it.
- **Confidence:** high · **Effort:** small
