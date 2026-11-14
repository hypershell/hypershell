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
