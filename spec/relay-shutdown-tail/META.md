# META — A relay run ends within seconds of its last result

> **Harness feedback log** for this feature — the producer artifact of the factory's self-improvement
> loop. Written by the lifecycle skills (`hs-feature` / `hs-plan` / `hs-build` / `hs-review`) when the
> **skillset itself** costs something; read by `hs-publish` (surfaced in the PR) and applied by
> `/hs-harness`. This file is **orthogonal** to the `GOAL → PLAN → TECH → REVIEW` spine — it is about
> the *toolchain*, not the feature — and is retained on merge like the rest of `spec/{slug}/`.

- **slug:** relay-shutdown-tail

## Friction findings

<!-- Real findings are appended below this line by the lifecycle skills. -->

## F1 — No guidance for promoting several seeds into one GOAL, or for a ROADMAP entry that names two seeds · seen again
`origin=hs-feature:step4 severity=low category=missing-guidance status=open target=.claude/skills/hs-feature/SKILL.md`
- **What happened:** `relay-shutdown-tail` (primary) and `relay-site-key-scan-access-denied` (a `kind: test` rider) were promoted together on the foreman's brief. The rider's ROADMAP entry names two seeds under one `· *status: …*` marker, and only one of them was promoted; the marker was split into one per seed by hand.
- **Skill cause:** As `spec/contended-writes/META.md` F1 and `spec/env-config-keys/META.md` F1: Step 4 describes one seed and one `**Seed:**` marker; nothing says how to mark a shared entry when one of its seeds is adopted, or how to carry a rider of another `kind`.
- **Recommended fix:** As recorded there, plus: an entry naming several seeds carries one status marker per seed.
- **Confidence:** high · **Effort:** small

## F2 — Step 1/3 assume local `develop` in the main checkout; cycles run in worktrees · seen again
`origin=hs-feature:step3 severity=low category=instruction status=open target=.claude/skills/hs-feature/SKILL.md`
- **What happened:** The worktree on `fix/relay-shutdown-tail` off `origin/develop` `f3fb4dd2` existed before this step; Step 1's "on `develop`" check and Step 3's `git switch -c` were skipped on the brief's instruction.
- **Skill cause:** As `spec/contended-writes/META.md` F2; the skill text is unchanged.
- **Recommended fix:** As recorded there.
- **Confidence:** high · **Effort:** small

## F3 — Promoting an `unshaped` seed does not say to re-verify its evidence against `develop` · seen again
`origin=hs-feature:step4 severity=medium category=instruction status=open target=.claude/skills/hs-feature/SKILL.md`
- **What happened:** The primary seed's evidence ("about 7 s in three of five runs") and its draft R3 (no tail over 5 s at the R8 size) were measured on the `relay-hardening` head. On `develop` the R8 size showed no hold in 6 of 6 runs, so the draft R3 would have passed before any fix; only a measurement the brief asked for (a size whose last site bundle is partial) showed the defect still live.
- **Skill cause:** As `spec/env-config-keys/META.md` F3: the `unshaped` bullet says to carry the evidence into Problem, not to reproduce it on current `develop` first, and "shaping only" discourages the probe.
- **Recommended fix:** As recorded there; also check that each draft acceptance criterion fails on current `develop` before it becomes part of the contract.
- **Confidence:** high · **Effort:** small
