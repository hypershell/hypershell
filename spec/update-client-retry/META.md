# META — `hs update --delete`, the client's bundle keys, and terminal statuses

> **Harness feedback log** for this feature — the producer artifact of the factory's self-improvement
> loop. Written by the lifecycle skills (`hs-feature` / `hs-plan` / `hs-build` / `hs-review`) when the
> **skillset itself** costs something; read by `hs-publish` (surfaced in the PR) and applied by
> `/hs-harness`. This file is **orthogonal** to the `GOAL → PLAN → TECH → REVIEW` spine — it is about
> the *toolchain*, not the feature — and is retained on merge like the rest of `spec/{slug}/`.

- **slug:** update-client-retry

## What worked well

- `hs-feature` Step 4's `kind: fix` rule (criteria as observable broken→fixed behaviour, not the
  suspected mechanism) kept R6 free of `increment_group`'s `attempt >= attempts` detail, which the
  plan owns.

## Friction findings

<!-- Real findings are appended below this line by the lifecycle skills. -->

## F1 — No guidance for promoting several seeds into one GOAL under a new slug · seen again
`origin=hs-feature:step4 severity=low category=missing-guidance status=open target=.claude/skills/hs-feature/SKILL.md`
- **What happened:** Three seeds were promoted under `update-client-retry`, a slug that is none of their stems, on the foreman's brief.
- **Skill cause:** As `spec/contended-writes/META.md` F1 and `spec/config-set-and-unread/META.md` F1: Argument Parsing takes `{slug}` from one seed's stem and Step 4 describes one seed.
- **Recommended fix:** As recorded there.
- **Confidence:** high · **Effort:** small

## F2 — Step 1/3 assume local `develop` in the main checkout; cycles run in worktrees · seen again
`origin=hs-feature:step3 severity=low category=instruction status=open target=.claude/skills/hs-feature/SKILL.md`
- **What happened:** The worktree on `fix/update-client-retry` off `origin/develop` `6a0c28ea` existed before this step; Step 1's "on `develop`" check and Step 3's `git switch -c` were skipped on the brief's instruction.
- **Skill cause:** As `spec/contended-writes/META.md` F2; the skill text is unchanged.
- **Recommended fix:** As recorded there.
- **Confidence:** high · **Effort:** small

## F3 — Promoting an `unshaped` seed does not say to re-verify its evidence against `develop` · seen again
`origin=hs-feature:step4 severity=medium category=instruction status=open target=.claude/skills/hs-feature/SKILL.md`
- **What happened:** Re-running the seeds' claims on `6a0c28ea` (as the brief asked) found two defects beside them (the legacy `hs update` form drops action options; a retry drops cores, memory and walltime), showed one seed claim holds only for the Python API, and found the seeds' cited docs contradict each other. The skill's "no code reads" and allowed-tools (no `uv run`, no throwaway site) would have carried the seeds unchecked.
- **Skill cause:** As `spec/env-config-keys/META.md` F3 and `spec/config-set-and-unread/META.md` F3.
- **Recommended fix:** As recorded there.
- **Confidence:** high · **Effort:** small

## F4 — Step 4's ROADMAP marker rule does not say what to do when a Seed line has no marker
`origin=hs-feature:step4 severity=low category=instruction status=open target=.claude/skills/hs-feature/SKILL.md`
- **What happened:** Of the three adopted seeds' `ROADMAP.md` entries, one `**Seed:**` line carried `· *status: unshaped*` and two carried no marker. The skill says to update the marker "where that entry's Seed line carries" one, which reads as leaving the other two unmarked, so the index would show one of three seeds adopted. The marker was added to all three, on the brief's "update their ROADMAP markers".
- **Skill cause:** The rule is conditional on a marker the `/hs-roadmap` convention does not always write, so adoption is visible in the index only for some seeds.
- **Recommended fix:** Say "set the entry's `**Seed:**` line marker to `· *status: adopted:{slug}*`, adding it when absent", and have `/hs-roadmap` write the marker on every new Seed line.
- **Confidence:** high · **Effort:** small
