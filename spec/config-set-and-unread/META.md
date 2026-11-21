# META — `hs config set` typing and unread configuration keys

> **Harness feedback log** for this feature — the producer artifact of the factory's self-improvement
> loop. Written by the lifecycle skills (`hs-feature` / `hs-plan` / `hs-build` / `hs-review`) when the
> **skillset itself** costs something; read by `hs-publish` (surfaced in the PR) and applied by
> `/hs-harness`. This file is **orthogonal** to the `GOAL → PLAN → TECH → REVIEW` spine — it is about
> the *toolchain*, not the feature — and is retained on merge like the rest of `spec/{slug}/`.

- **slug:** config-set-and-unread

## Friction findings

<!-- Real findings are appended below this line by the lifecycle skills. -->

## F1 — No guidance for promoting several seeds into one GOAL under a new slug · seen again
`origin=hs-feature:step4 severity=low category=missing-guidance status=open target=.claude/skills/hs-feature/SKILL.md`
- **What happened:** Two seeds (`config-set-coerces-text`, `config-keys-unread`) were promoted under `config-set-and-unread`, a slug that is neither one's stem, on the foreman's brief.
- **Skill cause:** As `spec/contended-writes/META.md` F1 and later cycles: Argument Parsing takes `{slug}` from one seed's stem and Step 4 describes one seed.
- **Recommended fix:** As recorded there.
- **Confidence:** high · **Effort:** small

## F2 — Step 1/3 assume local `develop` in the main checkout; cycles run in worktrees · seen again
`origin=hs-feature:step3 severity=low category=instruction status=open target=.claude/skills/hs-feature/SKILL.md`
- **What happened:** The worktree on `fix/config-set-and-unread` off `origin/develop` `d9901cf0` existed before this step; Step 1's "on `develop`" check and Step 3's `git switch -c` were skipped on the brief's instruction.
- **Skill cause:** As `spec/contended-writes/META.md` F2; the skill text is unchanged.
- **Recommended fix:** As recorded there.
- **Confidence:** high · **Effort:** small

## F3 — Promoting an `unshaped` seed does not say to re-verify its evidence against `develop` · seen again
`origin=hs-feature:step4 severity=medium category=instruction status=open target=.claude/skills/hs-feature/SKILL.md`
- **What happened:** Verifying the seeds' claims on `d9901cf0`, as the brief asked, found a fourth unread key (`server.attempts` under `hs cluster`) beside the seed's three, a second defect behind `client.ratelimit` (`-R` takes whole numbers, so a fractional key would break launched clients), and drifted line numbers. The skill's "shaping only, no code reads" and its allowed-tools (no `uv run`, no probe) would have carried the seed's list unchecked.
- **Skill cause:** As `spec/env-config-keys/META.md` F3: the `unshaped` bullet does not ask for the seed's reproduction to be re-run on current `develop`.
- **Recommended fix:** As recorded there; also allow a throwaway-site probe (`.agents/factory/bin/temp_site.sh`) in allowed-tools.
- **Confidence:** high · **Effort:** small
