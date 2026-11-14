# META — Contended writes

> **Harness feedback log** for this feature — the producer artifact of the factory's self-improvement
> loop. Written by the lifecycle skills (`hs-feature` / `hs-plan` / `hs-build` / `hs-review`) when the
> **skillset itself** costs something; read by `hs-publish` (surfaced in the PR) and applied by
> `/hs-harness`. This file is **orthogonal** to the `GOAL → PLAN → TECH → REVIEW` spine — it is about
> the *toolchain*, not the feature — and is retained on merge like the rest of `spec/{slug}/`.

- **slug:** contended-writes

## Friction findings

<!-- Real findings are appended below this line by the lifecycle skills. -->

## F1 — No guidance for promoting several seeds into one GOAL, or for a seed entry with no ROADMAP status marker · seen again
`origin=hs-feature:step4 severity=low category=missing-guidance status=open target=.claude/skills/hs-feature/SKILL.md`
- **What happened:** Two seeds were promoted under a slug that is neither one's stem; the second seed's `ROADMAP.md` entry had no `· *status: …*` marker (30 of 54 entries lack one), so the skill's "where that entry's **Seed:** line carries a marker" left it unmarked while the brief asked for both markers. I added the marker.
- **Skill cause:** As `spec/relay-hardening/META.md` F1 and `spec/relay-retire/META.md` F1: the skill assumes one seed whose stem is the slug, and it does not say whether a missing marker is added on promotion.
- **Recommended fix:** As recorded there, plus: on promotion, add the marker when the entry lacks one, so `/hs-roadmap` and readers see every adopted seed the same way.
- **Confidence:** high · **Effort:** small

## F2 — Step 1/3 assume local `develop` in the main checkout; cycles run in worktrees · seen again
`origin=hs-feature:step3 severity=low category=instruction status=open target=.claude/skills/hs-feature/SKILL.md`
- **What happened:** The worktree `.worktrees/contended-writes` on `fix/contended-writes` off `origin/develop` was created before this step ran; Step 1's "on `develop`" check and Step 3's `git switch -c` were skipped by the brief's instruction.
- **Skill cause:** Open in `spec/relay-hardening/META.md` F2 and earlier cycles' META files; the skill text is unchanged.
- **Recommended fix:** As recorded there.
- **Confidence:** high · **Effort:** small
