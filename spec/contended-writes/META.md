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

## F5 — `set_phase.py` re-serializes the whole frontmatter on its first run
`origin=hs-build:P1 severity=low category=tooling status=open target=.agents/factory/bin/set_phase.py`
- **What happened:** Advancing P1 (two fields: `status`, `current_phase`) rewrote every line of the hand-written frontmatter in the dumper's style (single quotes, wrapped `title`, `- id` dedented, folded `verify:` blocks re-flowed), a ~120-line diff; parsed, only the two fields differ.
- **Skill cause:** Step 5 mandates the script ("never hand-edit YAML"), and `hs-plan` writes the frontmatter in a style the script does not keep, so the first build commit of every cycle buries its state change in reformatting.
- **Recommended fix:** Have `hs-plan` emit the frontmatter through the same dumper (or `set_phase.py --touch` at plan time), or make the dumper keep the template's style.
- **Confidence:** high · **Effort:** small

## F6 — `next_phase.py` points at an external-wait phase while a later phase is buildable · seen again
`origin=hs-build:P3 severity=low category=tooling status=open target=.agents/factory/bin/next_phase.py`
- **What happened:** With P2 (CI red on the draft PR) pending as an external wait and P3 depending only on P1, `next_phase.py` reported P2 as next; P3 was built by explicit phase selection, and Step 5's `--current {next_id_or_done}` left `current_phase` on P2, a phase no build can run.
- **Skill cause:** As `spec/hypertable-update-retry/META.md` and earlier cycles ("no way to express an external wait in a TECH phase"): the FSM has no external-wait status, so the resume pointer cannot skip a wait to the next buildable phase.
- **Recommended fix:** As recorded there: an `external` phase flag (or `waiting` status) that `next_phase.py` skips when choosing the next buildable phase, reporting the wait separately.
- **Confidence:** high · **Effort:** small
