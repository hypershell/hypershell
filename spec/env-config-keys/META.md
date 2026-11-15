# META — Environment configuration keys

> **Harness feedback log** for this feature — the producer artifact of the factory's self-improvement
> loop. Written by the lifecycle skills (`hs-feature` / `hs-plan` / `hs-build` / `hs-review`) when the
> **skillset itself** costs something; read by `hs-publish` (surfaced in the PR) and applied by
> `/hs-harness`. This file is **orthogonal** to the `GOAL → PLAN → TECH → REVIEW` spine — it is about
> the *toolchain*, not the feature — and is retained on merge like the rest of `spec/{slug}/`.

- **slug:** env-config-keys

## Friction findings

<!-- Real findings are appended below this line by the lifecycle skills. -->

## F1 — No guidance for promoting several seeds into one GOAL under a new slug · seen again
`origin=hs-feature:step4 severity=low category=missing-guidance status=open target=.claude/skills/hs-feature/SKILL.md`
- **What happened:** Two seeds (`env-config-key-mapping`, `duration-units-config-keys`) were promoted under `env-config-keys`, a slug that is neither one's stem, on the foreman's brief.
- **Skill cause:** As `spec/contended-writes/META.md` F1 and earlier cycles: Argument Parsing takes `{slug}` from one seed's stem and Step 4 describes one seed.
- **Recommended fix:** As recorded there.
- **Confidence:** high · **Effort:** small

## F2 — Step 1/3 assume local `develop` in the main checkout; cycles run in worktrees · seen again
`origin=hs-feature:step3 severity=low category=instruction status=open target=.claude/skills/hs-feature/SKILL.md`
- **What happened:** The worktree on `fix/env-config-keys` off `origin/develop` `90ab2fc2` existed before this step; Step 1's "on `develop`" check and Step 3's `git switch -c` were skipped on the brief's instruction.
- **Skill cause:** As `spec/contended-writes/META.md` F2; the skill text is unchanged.
- **Recommended fix:** As recorded there.
- **Confidence:** high · **Effort:** small

## F3 — Promoting an `unshaped` seed does not say to re-verify its evidence against `develop`
`origin=hs-feature:step4 severity=medium category=instruction status=open target=.claude/skills/hs-feature/SKILL.md`
- **What happened:** The primary seed's headline defect (an all-digit `server.auth` crashes `hs server`) had been changed by #97 in between: `resolve_auth` now stringifies, so the crash is gone and a silent key rewrite remains. Its `_ENV` reproduction message, line numbers and cmdkit version had drifted too. Only the brief's "verify every seed claim" caught it, through probes the skill's "shaping only, no code reads" discourages.
- **Skill cause:** Step 4 asks a `shaped` seed to be re-confirmed against `develop`, but for an `unshaped` seed only says to carry its evidence into Problem, so a stale reproduction becomes the contract's motivation unchecked.
- **Recommended fix:** In the `unshaped` bullet: re-run the seed's reproduction (or a minimal probe) on current `develop` before carrying it, and cite the revision; say this is in scope despite "shaping only".
- **Confidence:** high · **Effort:** small

## F4 — No shared red-on-base harness; this plan reuses two other cycles' drive scripts · seen again
`origin=hs-plan:step6 severity=low category=tooling status=open target=.agents/factory/bin/`
- **What happened:** The brief asked for a tests-first phase with a recorded red list on `90ab2fc2`. P1's and P7's `verify:` call `spec/relay-hardening/drive/red_check.sh` and `spec/contended-writes/drive/rest_green.sh`, found by searching earlier cycles' `TECH.md`; neither the skill nor the TECH template names them.
- **Skill cause:** As `spec/relay-hardening/META.md` F5 and `spec/contended-writes/META.md` F4: Step 6 gives no pattern for proving "fails on <rev>", and the harness lives in two cycle folders.
- **Recommended fix:** As recorded there: move `red_check.sh`, `red_outcome.py` and `rest_green.sh` to `.agents/factory/bin/` and reference them from Step 6 and the TECH template.
- **Confidence:** high · **Effort:** small

## F5 — `hs-plan` dry-runs each drive mode only on the base, so checks past the first are never run
`origin=hs-build:P7 severity=low category=instruction status=open target=.claude/skills/hs-plan/SKILL.md`
- **What happened:** The plan's dry run ran every `env_drive.sh` mode on `90ab2fc2`, where each stops at its first new-behaviour check, as expected; no later check in any mode had been exercised. Three drive bugs shipped that way and were fixed in P2–P4 (a path that must exist for `HYPERSHELL_PYTHONPATH`, BSD `printenv` taking one name, a completion match read as a pattern); a fourth surfaced in P5: the `durations` mode and `test_duration_refused_at_start[client.heartrate]` ran `hs client` with no argument, which prints the usage and exits 1 before any check.
- **Skill cause:** Step 6 asks for a drive dry run but only on the base, where "fails at its first new-behaviour check" is the expected outcome, so it cannot show that the rest of a mode is sound; nothing asks for each command the drive or a refusal test runs to be shown reaching the code under test.
- **Recommended fix:** In Step 6, after the base dry run, run each drive mode once more with every check made to continue (e.g. an `ENV_DRIVE_CONTINUE=1` that reports instead of exiting), and for each command a drive or a refusal test runs, confirm on the base that it gets past argument parsing (not exit 1/2 with usage) before its expected failure.
- **Confidence:** high · **Effort:** small

**What worked well:** Writing the frontmatter through `_fsm.dump_document` (the writer `set_phase.py` uses) left it byte-stable under a re-dump, so P1's first state change will not reformat the file (`spec/contended-writes/META.md` F5).
