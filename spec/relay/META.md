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
