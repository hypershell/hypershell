# META — `hs update` integer group and the single-task form's selection options

> **Harness feedback log** for this feature — the producer artifact of the factory's self-improvement
> loop. Written by the lifecycle skills (`hs-feature` / `hs-plan` / `hs-build` / `hs-review`) when the
> **skillset itself** costs something; read by `hs-publish` (surfaced in the PR) and applied by
> `/hs-harness`. This file is **orthogonal** to the `GOAL → PLAN → TECH → REVIEW` spine — it is about
> the *toolchain*, not the feature — and is retained on merge like the rest of `spec/{slug}/`.

- **slug:** update-group-and-legacy-filters

## Friction findings

<!-- Real findings are appended below this line by the lifecycle skills. -->

## F1 — No guidance for promoting several seeds into one GOAL under a new slug · seen again
`origin=hs-feature:step-4 severity=low category=missing-guidance status=open target=.claude/skills/hs-feature/SKILL.md`
- **What happened:** Two seeds were promoted under `update-group-and-legacy-filters`, a slug that is neither stem, on the foreman's brief; Step 7's `git add issues/{slug}.md` names a file that does not exist, and the template's single `Seed:` line became a `Seeds:` list by hand.
- **Skill cause:** As `spec/update-client-retry/META.md` F1: Argument Parsing takes `{slug}` from one seed's stem and Steps 4 and 7 describe one seed.
- **Recommended fix:** As there.
- **Confidence:** high · **Effort:** small

## F2 — Promoting an `unshaped` seed does not say to re-verify its evidence against `develop` · seen again
`origin=hs-feature:step-4 severity=medium category=missing-guidance status=open target=.claude/skills/hs-feature/SKILL.md`
- **What happened:** Re-running both seeds on `03a02bd8` (as the brief asked) found the single-task form stores a text group too, `--desc` dropped beside the listed filters, five other integer columns storing text, and two seed claims that no longer hold as written (`none`/`null`/`nan` fail with a traceback rather than being stored; the "sequence after" dependency has landed). The skill's "no code reads" and allowed-tools (no `uv run`, no throwaway site) would have carried the seeds unchecked.
- **Skill cause:** As `spec/update-client-retry/META.md` F3.
- **Recommended fix:** As there.
- **Confidence:** high · **Effort:** small

## F3 — The tests-first red-list tooling and the verify bound live in other cycles' spec folders, unnamed by the skill · seen again
`origin=hs-plan:step6 severity=low category=missing-guidance status=open target=.claude/skills/hs-plan/SKILL.md`
- **What happened:** As `spec/update-client-retry/META.md` F5 and F6: this plan's verifies run `spec/relay-hardening/drive/red_check.sh`, `spec/contended-writes/drive/rest_green.sh` and `spec/update-client-retry/drive/bound.sh`, found by reading a previous cycle's `TECH.md`.
- **Skill cause:** As recorded there.
- **Recommended fix:** As recorded there.
- **Confidence:** high · **Effort:** small
