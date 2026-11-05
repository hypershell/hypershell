# META — HyperShell Relay: two-level scheduling across administrative sites

> **Harness feedback log** for this feature — the producer artifact of the factory's self-improvement
> loop. Written by the lifecycle skills (`hs-feature` / `hs-plan` / `hs-build` / `hs-review`) when the
> **skillset itself** costs something; read by `hs-publish` (surfaced in the PR) and applied by
> `/hs-harness`. This file is **orthogonal** to the `GOAL → PLAN → TECH → REVIEW` spine — it is about
> the *toolchain*, not the feature — and is retained on merge like the rest of `spec/{slug}/`.
>
> **Silence is the default.** The bar for a finding is one test: *was this the **skill's** fault — not
> mine, not the task's?* The blind `hs-review` correctness reviewer never reads this file.

- **slug:** relay

## Friction findings

<!-- Real findings are appended below this line by the lifecycle skills. -->

## F1 — Step 3 branches from local `develop`, which may be stale · seen again
`origin=hs-feature:step3 severity=medium category=instruction status=open target=.agents/skills/hs-feature/SKILL.md`
- **What happened:** A recurrence of `spec/zone-queue-primitive/META.md` F1 (itself a recurrence
  of `spec/group-advance-retry-gate/META.md` F1). Local `develop` was stale, so the cycle was
  opened with `git worktree add --no-track -b feature/relay .worktrees/relay origin/develop`
  instead of Step 3's `git switch -c {branch} develop`, and Step 1's "on `develop`" check could
  not pass in that worktree. `git worktree` is also outside the skill's `allowed-tools`.
- **Skill cause:** Unchanged from the original finding.
- **Recommended fix:** As in the original finding: branch from `origin/develop` with
  `--no-track`, and accept a fresh worktree at that commit as the clean-tree precondition.
- **Confidence:** high · **Effort:** small

## F2 — No way to promote part of a shaped seed and keep the rest alive as `shaped` · seen again
`origin=hs-feature:step4 severity=medium category=missing-guidance status=open target=.agents/skills/hs-feature/SKILL.md`
- **What happened:** A recurrence of `spec/zone-queue-primitive/META.md` F2. The `relay` seed is
  shaped as two phases with 18 criteria, and the calendar fits only part of the first. Step 4
  says to adopt a `shaped` seed "largely as written" and not re-litigate it, while the size
  circuit-breaker says to offer a pilot + follow-ups split; neither says how the two combine, nor
  where a shaped remainder goes once the seed is marked `adopted:relay`. The GOAL therefore keeps
  all of R1–R18 for traceability, puts the cut to the maintainer as an open clarification, and
  names a new shaped seed (`issues/relay-topology.md`) to be written only once the cut is ruled.
- **Skill cause:** Unchanged from the original finding; this occurrence adds the circuit-breaker
  interaction and a calendar deadline as a second trigger for a partial promotion.
- **Recommended fix:** As in the original finding, plus: say that for a `shaped` seed over the
  breaker, the split is offered as an open clarification with options, and the remainder is
  re-seeded `shaped` (carrying the seed's dated clarifications) in the same commit as the ruling.
- **Confidence:** high · **Effort:** small

## F3 — Planning on a base that does not yet hold the GOAL's prerequisites · seen again
`origin=hs-plan:step-3 severity=medium category=missing-guidance status=open target=.agents/skills/hs-plan/SKILL.md`
- **What happened:** A recurrence of `spec/zone-dispatch/META.md` F2, one level deeper. Relay must
  be planned against develop + Z1 + Z2, none of which is merged, and Z1 and Z2 conflict in
  `server.py`, `client.py` and `data/model.py`. The planner built a read-only merge preview
  (`git merge-tree --write-tree` + `git archive` into scratch) for the research briefs and invented
  the same fail-fast guard in P1's `verify:` and a `blocked_reason`.
- **Skill cause:** Step 1 checks the branch and GOAL, not the base; Step 3 gives research no way to
  read a tree that is not checked out.
- **Recommended fix:** As in the original finding, plus a Step 3 note: when the base is a stack of
  unmerged branches, build a read-only merge preview for the briefs and tell them which side of
  each conflicted file is authoritative.
- **Confidence:** med · **Effort:** small

## F4 — Step 6's size circuit-breaker has no rule for scope a maintainer already ruled
`origin=hs-plan:step-6 severity=low category=instruction status=open target=.agents/skills/hs-plan/SKILL.md`
- **What happened:** The roadmap needs ten phases. Step 6 says to pause and reconsider with the
  human above ~8, but the GOAL records the maintainer's ruling (C1) that the whole scope is in,
  made knowing it was tight, and the foreman asked for small phases for incremental review. The
  planner recorded the breaker as an open ruling (N6) instead of pausing.
- **Skill cause:** The breaker treats phase count as a proxy for scope, and does not say what to do
  when scope is already ruled and the count comes from slicing finer for review.
- **Recommended fix:** Say that when the GOAL carries a scope ruling, the breaker becomes a recorded
  plan-gate ruling with the phase count and the reason, not a pause.
- **Confidence:** med · **Effort:** small
