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

## F5 — The rubric checks new process tests by mutation, not for timing dependence
`origin=hs-review:step-2 severity=low category=missing-guidance status=open target=.agents/factory/review-rubric.md`
- **What happened:** The review-2 remediation's C2-1 process test passed three runs in a row on an
  idle host. It also passed the cycle-3 reviewer's first full-suite run and was mutation-killed.
  Under eight concurrent copies it failed 3 of 8, because its injected fault fires on the
  collector's empty bundles, not on the result it claims to wait for (REVIEW C3-2). Nothing in the
  rubric or the build's verify asks a new process test to run under load. The reviewer found the
  flake only by happening to add a concurrency repeat.
- **Skill cause:** The rubric asks for mutation and for executed evidence. It says nothing about
  repeating new timing-sensitive integration tests concurrently, so "three runs, no flake"
  (sequential, idle) passes as evidence.
- **Recommended fix:** In the rubric (and `hs-build`'s gate for process tests), require each new
  integration test that drives real processes to pass N concurrent copies, for example 8 with
  distinct `--basetemp`, run in the foreground (a background job inherits SIGINT ignored). The
  pass count goes in the record.
- **Confidence:** med · **Effort:** small

**What worked well:** fault injection by a pre-import patch script, with the process marked a relay
so it does not re-execute, plus a trigger file to arm the fault. It turned "any server thread dies"
into seven real-process runs in under a minute.

## F6 — No rule for a "run A" phase that is built but waits on an external run
`origin=hs-build:P7 severity=low category=missing-guidance status=open target=.agents/skills/hs-build/SKILL.md`
- **What happened:** P7 builds the multi-node drive and passes its verify, but R21 is shown only by the
  foreman's later run; the foreman had to rule that P7 stays `in_progress` (waiting on the run), not `done`.
- **Skill cause:** Step 5 knows only "verify green → `done`, advance `current_phase`"; the external-wait
  convention in the TECH field reference covers the judging phase (`blocked` until evidence) but not the
  build-and-hand-back phase before it.
- **Recommended fix:** in Step 5, add: for a phase whose TECH body hands back to an external run, a green
  verify leaves it `in_progress` (hill `crest`), `current_phase` unchanged, with the hand-back recorded in
  the body; the judging phase's arrival of evidence closes it.
- **Confidence:** med · **Effort:** small

## F7 — The ≤3-cycle bound is graded against one durable counter, but a run may bound cycles per scope · seen again
`origin=hs-review:Step 4 severity=low category=template status=open target=.agents/skills/hs-review/SKILL.md`
- **What happened:** after cycle 3 approved P1–P6, the foreman opened a new review scope (P7–P9) with
  a fresh count of 3. `set_phase.py` keeps one `review.cycle`, which moves to 4, so the record carries
  "cycle 4 (scope: P7–P9, fresh count 1 of 3)" in prose only.
- **Skill cause:** Step 4 and the rubric's *Bounded loop* grade the bound against `review.cycle` alone
  and assume one review scope per branch. Interim scoped reviews (cycle 1 here was "interim, after
  P5") make that counter count scopes as well as rounds.
- **Recommended fix:** keep a per-scope count beside `cycle` in the TECH frontmatter (e.g.
  `review.scope` and `review.scope_cycle`), reset by a `set_phase.py --new-scope NAME` flag, and grade
  the bound against it; the rubric and Step 4 say so.
- **Confidence:** med · **Effort:** small
