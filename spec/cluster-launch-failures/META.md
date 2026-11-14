# META — A cluster whose clients all fail says so and exits; one advertised address for every launch mode

> **Harness feedback log** for this feature — the producer artifact of the factory's self-improvement
> loop. Written by the lifecycle skills (`hs-feature` / `hs-plan` / `hs-build` / `hs-review`) when the
> **skillset itself** costs something; read by `hs-publish` (surfaced in the PR) and applied by
> `/hs-harness`. This file is **orthogonal** to the `GOAL → PLAN → TECH → REVIEW` spine — it is about
> the *toolchain*, not the feature — and is retained on merge like the rest of `spec/{slug}/`.

- **slug:** cluster-launch-failures

## Friction findings

<!-- Real findings are appended below this line by the lifecycle skills. -->

## F1 — No guidance for promoting several seeds into one GOAL under a new slug · seen again
`origin=hs-feature:step4 severity=low category=missing-guidance status=open target=.claude/skills/hs-feature/SKILL.md`
- **What happened:** This cycle promotes two unshaped seeds (`cluster-waits-when-every-client-fails`, primary, and `advertised-host-ignores-bind`) under a slug that is neither stem. Argument Parsing takes `{slug}` from the one seed's file stem, Step 7 adds `issues/{slug}.md`, and the GOAL template's Related materials has one `Seed:` line; I followed the `relay-retire` precedent by hand for both statuses, both ROADMAP markers and a two-seed `Seed:` line.
- **Skill cause:** Same as `spec/relay-retire/META.md` F1: the promotion steps assume one seed whose stem is the slug.
- **Recommended fix:** As there: accept several `issues/*.md` arguments (first is primary), let the slug differ from the primary stem, make Step 7 add every adopted seed, and let the template's `Seed:` line list several.
- **Confidence:** high · **Effort:** small

## F2 — Step 1/3 assume local `develop` in the main checkout; cycles run in worktrees · seen again
`origin=hs-feature:step3 severity=low category=instruction status=open target=.claude/skills/hs-feature/SKILL.md`
- **What happened:** The branch and worktree were created by the foreman before this step (`.worktrees/cluster-launch-failures`, `fix/cluster-launch-failures` off `origin/develop`); Step 1's "on `develop`" check and Step 3's `git switch -c` do not apply, and were skipped on the invocation's instruction.
- **Skill cause:** Same as `spec/relay-retire/META.md` F2: the steps predate the worktree practice.
- **Recommended fix:** As there.
- **Confidence:** high · **Effort:** small

## F3 — REVIEW template's human-gate list omits `core/tls.py` and `cluster/remote|ssh.py`
`origin=hs-review:step3 severity=low category=template status=open target=.agents/factory/templates/REVIEW.md`
- **What happened:** This cycle's CONFIRMED findings run through `cluster/remote.py`/`cluster/ssh.py`. The template's "Human-gate triggers" section lists only `data/model.py`, `server.py`, `client.py`, `core/queue|fsm|thread|signal.py`, so filling it in from the template alone would have missed the gate; I took the list from the rubric and SKILL.md instead.
- **Skill cause:** The template drifted from `review-rubric.md` § Mandatory human sign-off gate and `invariants.md` (both include `core/tls.py` and `cluster/remote|ssh.py`).
- **Recommended fix:** Make the template name the rubric's list, or point to it rather than repeat it.
- **Confidence:** high · **Effort:** small
