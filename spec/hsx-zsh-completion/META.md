# META — Fix Zsh completion for `hsx` / `hs cluster`

> **Harness feedback log** for this feature. Silence is the default; the bar is *was this the skill's
> fault?* Read by `hs-publish` (surfaced in the PR) and applied by `/hs-harness`.

- **slug:** hsx-zsh-completion

## What worked well

- `hs-plan:Step 3` — for a small fix, the guidance to skip the subagent fan-out and instead "do a
  couple of targeted reads/tests yourself" + "verify by driving the CLI" was exactly right: a
  hands-on real-zsh reproduction (not a research fan-out) is what exposed the true root cause.

## Friction findings

## F1 — Fix GOAL encoded an unverified root-cause hypothesis in an acceptance criterion
`origin=hs-plan:step4 severity=low category=steering status=open target=.claude/skills/hs-feature/SKILL.md`
- **What happened:** the shaped `GOAL.md` (from `/hs-feature`) baked the reporter's suspected
  mechanism into the contract — the *Problem* blamed the `(1)` exclusion form and asserted "submit
  works", and **R4** required the fix to "not use two conflicting, one-broken spec forms". `/hs-plan`
  disproved all of it (real cause: an unescaped `]` in the option description; `hs submit` is broken
  too). R4 then had to be *reinterpreted* to its intent in `PLAN.md` because the correct fix leaves two
  different-but-both-valid spec forms.
- **Skill cause:** `/hs-feature` is shaping-only and rightly does not root-cause, but it lacks a guard
  steering acceptance criteria to be **observable-outcome-only** — especially for `kind: fix`, where the
  root cause is unverified at shaping time. A criterion phrased as a mechanism ("does not use two
  conflicting spec forms") is not a stable, observable contract.
- **Recommended fix:** in `hs-feature` (Step 4) and/or the `GOAL.md` template, add: "For fixes, phrase
  acceptance criteria as observable behavior (what the user sees), never as the suspected
  cause/mechanism — the cause is unverified until `/hs-plan`."
- **Confidence:** med · **Effort:** small
