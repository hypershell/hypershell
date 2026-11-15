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

## F3 — High-blast-radius research exception vs a GOAL that says no fan-out is needed · seen again
`origin=hs-plan:step3 severity=low category=instruction status=open target=.claude/skills/hs-plan/SKILL.md`
- **What happened:** The GOAL (`appetite: small`) says the change "does not need a research fan-out", while Step 3's high-blast-radius and GOAL-deferred-unknowns exceptions both require one "regardless of appetite/kind"; only the diagnostic exception says the GOAL wins a disagreement. The two questions needed live database probes with the sandbox off, so they ran in-session as two briefs plus probes, against the letter of the step.
- **Skill cause:** As `spec/server-initdb/META.md` F3 and `spec/tls-client-address-family/META.md` F1: no depth scale and no in-session path for a diagnosed small fix, and no rule for which wins when the GOAL rules on research depth.
- **Recommended fix:** As recorded there, plus: say whether a GOAL's explicit research ruling overrides the blast-radius exception (it should not drop `research/`, only the parallel fan-out).
- **Confidence:** med · **Effort:** small

## F4 — No shared red-on-base harness; this plan reuses another cycle's `drive/red_check.sh` · seen again
`origin=hs-plan:step6 severity=low category=tooling status=open target=.agents/factory/bin/`
- **What happened:** R5 requires each test to fail before the fix. The verify gates call `spec/relay-hardening/drive/red_check.sh` with `RED_BASE_REV`/`RED_LIST`, because nothing under `.agents/factory/bin/` does this, and add a `rest_green.sh` beside it.
- **Skill cause:** As `spec/relay-hardening/META.md` (the generic `red_check.sh` recommendation): Step 6 and the TECH template still give no way to prove "fails on <rev>".
- **Recommended fix:** As recorded there: move `red_check.sh` + `red_outcome.py` (and a deselect-the-red-list runner) to `.agents/factory/bin/` and reference them from Step 6.
- **Confidence:** high · **Effort:** small
