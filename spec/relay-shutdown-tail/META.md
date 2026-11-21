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

## F4 — `verify:` guidance has no guard against a command that never returns · seen again
`origin=hs-plan:step6 severity=low category=missing-guidance status=open target=.agents/skills/hs-plan/SKILL.md`
- **What happened:** The host has no `timeout(1)` and the suite has no per-test timeout, yet every verify here runs integration tests or `hs cluster`, which wait out a 60 s bundle wait or a 600 s back-off on the base. The plan wrote its own `drive/bounded.py` (process group; SIGINT, SIGTERM, SIGKILL; exit 124) and routed every verify through it; research brief 03 had meanwhile proposed a perl one-liner for the same job.
- **Skill cause:** As `spec/group-advance-strands-lower-group/META.md` F3: Steps 4 and 6 steer verification to CLI drives and integration tests but say nothing about bounding them, and the factory has no helper, so each plan (and each research agent) reinvents one.
- **Recommended fix:** As recorded there: a factory `bin/` bound helper (this cycle's `drive/bounded.py` is a candidate) and one line in Step 6 and the TECH template requiring it for every verify that runs integration tests or `hs`/`hsx`.
- **Confidence:** high · **Effort:** small

## F5 — A red list that holds a test-only fix cannot be re-proved red after the fix lands
`origin=hs-build:P7 severity=medium category=steering status=open target=.agents/skills/hs-plan/SKILL.md`
- **What happened:** P7's verify re-runs `red_check.sh` on `f3fb4dd2` with `drive/red.txt`. The checker puts the base build's `src` first and runs *this tree's* tests. The R7 line's fix lives in the test file itself (`read_process` in `tests/test_relay_workload.py`, P4), so on the head the `[AccessDenied]` test passes on the base `src` (NOT RED, pytest exit 0) and the gate fails though nothing regressed. It was RED at P1 (3 of 3), before P4 moved the fix into the test file. The plan's dry-run placed the probes in `tests/` without the fix, so it could not see this.
- **Skill cause:** The planning guidance puts every regression test on the red list and re-proves the list at the end, with no word on fixes that are themselves test code: for those the base build is not where the defect lives, and only the tests-first commit can show them red.
- **Recommended fix:** In `hs-plan` Step 6 (and the TECH template): a test-only fix's red proof is taken at the tests-first phase only; the final phase's red list leaves those lines out (or `red_check.sh` grows a way to take the tests from a named commit). Not a weakening: the P1 proof stays the record.
- **Confidence:** high · **Effort:** small
