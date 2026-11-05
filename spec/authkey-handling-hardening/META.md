# META — The queue authentication key is never exposed to other users on client nodes

> **Harness feedback log** for this feature — the producer artifact of the factory's self-improvement
> loop. Written by the lifecycle skills (`hs-feature` / `hs-plan` / `hs-build` / `hs-review`) when the
> **skillset itself** costs something; read by `hs-publish` (surfaced in the PR) and applied by
> `/hs-harness`. This file is **orthogonal** to the `GOAL → PLAN → TECH → REVIEW` spine — it is about
> the *toolchain*, not the feature — and is retained on merge like the rest of `spec/{slug}/`.

- **slug:** authkey-handling-hardening

## Friction findings

## F1 — Hidden-lane promotion derives the public slug from the seed's file stem
`origin=hs-feature:step-2 severity=medium category=instruction status=open target=.agents/skills/hs-feature/SKILL.md`
- **What happened:** Argument Parsing says a `.security/issues/<slug>.md` promotion takes `{slug}` from the file stem. A hidden-lane stem can name the weakness, and the slug becomes the public branch name, spec directory and commit subject. The maintainer's run brief had to override the rule with a neutral, outcome-named slug.
- **Skill cause:** Step 4 says the public GOAL must not republish a live mechanism, but Step 2 has no matching rule for the slug, so following the skill literally publishes what Step 4 protects.
- **Recommended fix:** For a `.security/issues/` promotion, require a neutral slug that names the hardening outcome, confirmed with the human, and record the seed's own stem only inside `.security/`.
- **Confidence:** high · **Effort:** small

## F2 — Promotion step says to commit the seed's status edit, which is impossible in the security lane
`origin=hs-feature:step-4 severity=low category=instruction status=open target=.agents/skills/hs-feature/SKILL.md`
- **What happened:** Step 4 and Step 7 say to set the seed's `status: adopted:{slug}` and commit it with the GOAL. `.security/` is gitignored, so the edit is made in place, left uncommitted, and has to be reported to whoever keeps the lane's records.
- **Skill cause:** The promotion and commit steps assume the public `issues/` lane. The security-lane sentence at the end of Step 4 covers the GOAL's content, not the bookkeeping.
- **Recommended fix:** Add a security-lane variant: edit the `.security/` seed's status (and the `.security/ROADMAP.md` marker, if any) in the main checkout, never stage it, and name the edit in the Step 8 report.
- **Confidence:** high · **Effort:** small

## F3 — Research fan-out has no rule for security-lane cycles
`origin=hs-plan:step-3 severity=medium category=missing-guidance status=open target=.agents/skills/hs-plan/SKILL.md`
- **What happened:** The high-blast-radius exception required a research fan-out, written to
  `spec/{slug}/research/`, which is public and committed. On a security-lane cycle the planner has
  to decide by hand which findings may go in public briefs and which belong in `.security/`. The
  run brief had to supply that rule.
- **Skill cause:** Step 3 and Step 4 assume that every research finding can be published. Unlike
  `AGENTS.md`'s security-lane rule, the skill never says where hidden-lane planning detail goes, or
  that public briefs and `PLAN.md` describe only the hardened behaviour.
- **Recommended fix:** Add a security-lane variant to Steps 3–4 and 8. When the GOAL has
  `lane: security`, public briefs and `PLAN.md`/`TECH.md` state only the fixed behaviour and cite
  `docs/security.rst` for anything before it. Rationale that names the exposure goes into the
  `.security/issues/` seed (never staged), and the Step 9 report names that file by path.
- **Confidence:** high · **Effort:** small
