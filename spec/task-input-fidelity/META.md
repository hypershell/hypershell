# META — Every task the input gives runs as given: date-like lines run as text, and a streamed run waits for its input to end

> **Harness feedback log** for this feature — the producer artifact of the factory's self-improvement
> loop. Written by the lifecycle skills (`hs-feature` / `hs-plan` / `hs-build` / `hs-review`) when the
> **skillset itself** costs something; read by `hs-publish` (surfaced in the PR) and applied by
> `/hs-harness`. This file is **orthogonal** to the `GOAL → PLAN → TECH → REVIEW` spine — it is about
> the *toolchain*, not the feature — and is retained on merge like the rest of `spec/{slug}/`.

- **slug:** task-input-fidelity

## Friction findings

<!-- Real findings are appended below this line by the lifecycle skills. -->

## F1 — No guidance for promoting several seeds into one GOAL under a new slug · seen again
`origin=hs-feature:step4 severity=low category=missing-guidance status=open target=.claude/skills/hs-feature/SKILL.md`
- **What happened:** Two unshaped seeds (`date-like-args-coerced`, primary, and `stream-run-stops-before-input-ends`) promoted under a slug that is neither stem. Argument Parsing takes `{slug}` from one seed's stem, Step 7 adds only `issues/{slug}.md`, and the template has one `Seed:` line; both statuses, both ROADMAP markers and a two-seed `Seed:` line were done by hand. The primary seed's ROADMAP line had no `· *status: …*` marker at all; one was added so the index matches the seed, which Step 4 ("where that entry's `**Seed:**` line carries a marker") neither asks for nor forbids.
- **Skill cause:** Same as `spec/cluster-launch-failures/META.md` F1 and `spec/relay-retire/META.md` F1: the promotion steps assume one seed whose stem is the slug.
- **Recommended fix:** As there; also say whether a missing ROADMAP marker is added on adoption.
- **Confidence:** high · **Effort:** small

## F2 — Step 1/3 assume local `develop` in the main checkout; cycles run in worktrees · seen again
`origin=hs-feature:step3 severity=low category=instruction status=open target=.claude/skills/hs-feature/SKILL.md`
- **What happened:** The foreman created the worktree and `fix/task-input-fidelity` off `origin/develop` before this step; Step 1's "on `develop`" check and Step 3's `git switch -c` did not apply and were skipped on the invocation's instruction.
- **Skill cause:** Same as `spec/cluster-launch-failures/META.md` F2: the steps predate the worktree practice.
- **Recommended fix:** As there.
- **Confidence:** high · **Effort:** small

## What worked well — hs-plan
- **Step 3's high-blast-radius exception paid for itself.** The GOAL said shaping left "no open research
  question" and the obvious stream fix was a one-term change; the scoped two-brief fan-out the
  exception forced found that the one-term change still drops a late line after any failed task (the
  failed-group halt), which the design now avoids. `origin=hs-plan:step3`

## F3 — The human-gate trigger list omits the lifecycle invariants a scheduler change touches
`origin=hs-review:step4 severity=medium category=instruction status=open target=.agents/factory/review-rubric.md`
- **What happened:** The only CONFIRMED finding (F1 in `REVIEW.md`) is prose in the branch's §3 invariant record about the scheduler's stop conditions and group gating. The rubric's gate lists core files and "security / DB-lifecycle" invariants by name (auth, TLS, CANCEL_STATUS/exit_status filters, `completion_time`, one retry row, `in_memory` guards); scheduler stop conditions, group gating and the §6 shutdown order are not on it, and the invocation had to say they count. Nor does the rubric say whether a finding confined to `invariants.md`/`AGENTS.md` text "touches" an invariant.
- **Skill cause:** The gate list predates changes to the scheduler's lifecycle, and it is silent on findings in the invariant record itself.
- **Recommended fix:** Add the scheduler stop conditions, group gating (§3) and the shutdown order (§6) to the gate list, and say that a CONFIRMED finding in the text of a gated invariant triggers the gate. This widens the gate; it does not weaken it.
- **Confidence:** med · **Effort:** small

## F4 — The review's base is local `develop`; worktree cycles need `origin/develop`
`origin=hs-review:step1 severity=low category=instruction status=open target=.claude/skills/hs-review/SKILL.md`
- **What happened:** The injected diffstat and Step 2's diff use `develop...HEAD`. In a worktree the local `develop` can lag `origin/develop`, so the diff the blind reviewer gets would carry unrelated hunks. The invocation said to fetch and diff against `origin/develop`, and the review did.
- **Skill cause:** Same root as F2 (hs-feature): the steps predate the worktree practice.
- **Recommended fix:** Fetch in Step 1 and use `origin/{base}` for the diffstat, Step 2's diff and the merge check.
- **Confidence:** high · **Effort:** small
