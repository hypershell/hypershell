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
