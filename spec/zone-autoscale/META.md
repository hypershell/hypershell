# META — Zone-aware autoscaling (zone, cycle 3)

> **Harness feedback log** for this feature — the producer artifact of the factory's self-improvement
> loop. Written by the lifecycle skills (`hs-feature` / `hs-plan` / `hs-build` / `hs-review`) when the
> **skillset itself** costs something; read by `hs-publish` (surfaced in the PR) and applied by
> `/hs-harness`. This file is **orthogonal** to the `GOAL → PLAN → TECH → REVIEW` spine — it is about
> the *toolchain*, not the feature — and is retained on merge like the rest of `spec/zone-autoscale/`.
>
> **Silence is the default.** The bar for a finding is one test: *was this the **skill's** fault — not
> mine, not the task's?* A merely-hard task, a self-inflicted error, or a one-off content/code issue
> (that belongs in `GOAL.md` / `REVIEW.md`) is **not** a finding. The blind `hs-review` correctness
> reviewer never reads this file — it would leak author intent.

- **slug:** zone-autoscale

## What worked well

Brief, optional reinforcement: a part of a skill / the harness that materially helped, so `/hs-harness`
knows what **not** to change. One line each, naming the skill/step. Skip the section entirely if
nothing stands out.

- `hs-feature` Step 4, "Promoting an issue" (`shaped`): adopt as written, re-confirm against `develop`, cite drift. It kept a
  three-cycle seed's settled clarifications out of re-litigation and pointed the work at line drift.
- `hs-plan` Step 3, briefs written to `research/NN-topic.md` by parallel subagents: three briefs (signal,
  relay seam, launcher/verify) came back with file:line findings and a rehearsed drive, so the plan
  synthesized from files, not transcripts.

## Friction findings

Zero or more findings, appended below — each a markdown **section** so appending is a low-corruption
operation and a stdlib parser reads them (`uv run python .agents/factory/bin/meta_status.py
spec/zone-autoscale/META.md`). Skills always write `status=open`; only `/hs-harness` flips it. `target` is a
best-guess file with **no line number** (re-derive the exact edit at apply time to avoid staleness). If
an equivalent finding already exists, append "· seen again" to its title instead of duplicating —
recurrence is signal, not bloat.

Field enums — `severity`: `high` (a safety / gate / correctness gap) `| medium | low`; `category`:
`instruction | steering | tooling | template | missing-guidance`; `status`: `open` (written by skills)
`| applied | rejected | deferred` (written by `/hs-harness`).

Schema (copy one block per finding, appending it **after** this fence — the fence is illustrative and
is skipped by the parser):

```markdown
## F1 — <one-line title of the skillset problem>
`origin=<skill>:<step> severity=<high|medium|low> category=<instruction|steering|tooling|template|missing-guidance> status=open target=<best-guess file>`
- **What happened:** <what the skill made you do, or fail to do>.
- **Skill cause:** <why this is the instructions' fault — not yours, not the task's>.
- **Recommended fix:** <the concrete change to the skill / template / script>.
- **Confidence:** <high|med|low> · **Effort:** <small|medium|large>
```

<!-- Real findings are appended below this line by the lifecycle skills. -->

## F1 — Step 3 branches from local `develop`, which Step 1 allows to be behind `origin`
`origin=hs-feature:step-3 severity=low category=instruction status=open target=.agents/skills/hs-feature/SKILL.md`
- **What happened:** the run started in a worktree detached at `origin/develop`; local `develop` was two commits behind. Step 3's `git switch -c {branch} develop` would have started the cycle on the stale base, so the branch was created from `HEAD` instead.
- **Skill cause:** Step 1.2 says a behind `develop` is "not fatal" and only to note it, but Step 3 hard-codes `develop` as the start point, so the note never changes the outcome and the GOAL's drift citations can be taken against code the branch does not contain.
- **Recommended fix:** in Step 3, branch from the commit the pre-flight confirmed (`origin/develop` after the fetch, or `HEAD` when it equals it), or make Step 1.2 fast-forward `develop` before branching.
- **Confidence:** med · **Effort:** small

## F2 — No rule for R-ID numbering when a seed is promoted in several cycles
`origin=hs-feature:step-4 severity=low category=missing-guidance status=open target=.agents/skills/hs-feature/SKILL.md`
- **What happened:** the `zone` seed's remaining R13–R17 had to become this GOAL's R-IDs. The seed says its numbering "is kept so citations elsewhere still resolve"; the template numbers from `R1`; Z2 renumbered with the seed ID in brackets. The precedent was followed (R1–R5 with `[seed R13]`…), by inference.
- **Skill cause:** "Promoting an issue" covers `shaped` seeds but not a seed split across cycles, so the numbering convention lives only in an earlier GOAL.
- **Recommended fix:** add one line to Step 4: when promoting part of a multi-cycle seed, number from `R1` and give the seed's R-ID in brackets on each criterion.
- **Confidence:** med · **Effort:** small

## F3 — No rule for a gate question that arises during planning when the rest of the plan stands
`origin=hs-plan:step-4 severity=low category=missing-guidance status=open target=.agents/skills/hs-plan/SKILL.md`
- **What happened:** two R-IDs disagreed at one point. R3 says `--zone` is appended to every launch; R4 says the zone-less case is identical to today. One design choice decides it, and the rest of the plan does not depend on it. The skill says to STOP only for markers in `GOAL.md` and for an unavoidable invariant conflict. So the plan was committed with a `[NEEDS CLARIFICATION]` in PLAN §2.5 and the question was raised at the sign-off gate, by inference.
- **Skill cause:** Steps 4–6 do not say whether a plan may carry a scoped `[NEEDS CLARIFICATION]` to the gate, or whether `hs-build` must refuse to start while one is open.
- **Recommended fix:** add to Step 4 or 9: a design question confined to named phase items may be committed as a `[NEEDS CLARIFICATION]` in PLAN §5 with the proposed reading, listed in the Step 9 report. `hs-build` stops before any phase whose checklist names it until it is resolved.
- **Confidence:** med · **Effort:** small

## F4 — Step 2 hands the blind reviewer "the full text of invariants.md" without saying which version, when the branch edits it
`origin=hs-review:step-2 severity=medium category=missing-guidance status=open target=.agents/skills/hs-review/SKILL.md`
- **What happened:** this branch adds an §11 bullet to `.agents/factory/invariants.md` (and a matching `AGENTS.md` paragraph) naming the design's own seams. The skill says to inline "the full text of `invariants.md`" and is silent on which copy. The branch copy would hand the reviewer author-written design names as if they were ground truth. The orchestrator inlined the base copy (`git show origin/develop:…`) and told the reviewer to grade the branch's edits as part of the diff, by inference.
- **Skill cause:** Step 2 and the rubric's "What the reviewer sees" assume `invariants.md` and `AGENTS.md` are fixed inputs. When a cycle edits them in lockstep (as §12 asks), the branch copy is both a graded claim and a leak of author intent past the `spec/` exclusion.
- **Recommended fix:** in Step 2, inline `invariants.md` from `{base}` (`git show {base}:.agents/factory/invariants.md`). Tell the reviewer that any branch edit to `invariants.md` or `AGENTS.md` is part of the diff, to be checked against the code like a docs change.
- **Confidence:** high · **Effort:** small

## F5 — Step 1's contract-drift check counts the plan gate's dated clarifications as mid-build drift
`origin=hs-review:step-1 severity=low category=instruction status=open target=.agents/skills/hs-review/SKILL.md`
- **What happened:** `git log {base}..HEAD -- spec/{slug}/GOAL.md` listed the shaping commit plus two `hs-plan` commits. Those commits append the gate's dated clarifications, which `hs-plan` sanctions. The skill reads "anything beyond the original shaping commit" as the contract moving mid-build, to be confirmed with the human before grading. All three commits predate the first build commit, and the orchestrator, a subagent with no way to ask, recorded that rather than stopping.
- **Skill cause:** the drift test draws its line at the shaping commit, where the build starts at the first `hs-build` commit. Every cycle whose plan gate records a clarification therefore trips a false alarm that blocks on a human.
- **Recommended fix:** draw the line at the first build commit. Surface only `GOAL.md` commits after it, and note any earlier clarification commits in `REVIEW.md`'s pre-flight without stopping.
- **Confidence:** high · **Effort:** small

## F6 — The skill's base is the local `develop`, which was stale again · seen again (other cycles) · seen again (review cycle 2)
`origin=hs-review:step-1 severity=medium category=instruction status=open target=.agents/skills/hs-review/SKILL.md`
- **What happened:** the injected diffstat and Step 1's `base` resolve to the local `develop` (`5f22298`), which differs from `origin/develop` (`1ea9145`, the branch's merge base). The foreman's brief named `origin/develop`, so the drift check, the graded diff and the reviewer brief all used it.
- **Skill cause:** "Current state" runs `git diff --stat develop...HEAD`, and Step 1 defaults `base` to the branch name `develop`, with no fetch and no comparison against `origin`. The same finding is open in several earlier cycles' META files.
- **Recommended fix:** resolve `{base}` to `origin/{base}` after a fetch (or to `git merge-base HEAD origin/{base}`), and use that SHA in the diffstat, the drift check and the reviewer's diff command.
- **Confidence:** high · **Effort:** small

## F7 — Remediation mode says nothing on where the human gate's rulings go, while GOAL is "locked"
`origin=hs-build:P3 severity=low category=missing-guidance status=open target=.agents/skills/hs-build/SKILL.md`
- **What happened:** review cycle 1 tripped the human gate, and the maintainer's rulings (F1–F4) had to land as dated GOAL Clarification lines in the remediation commit. The foreman's brief said so; the skill does not.
- **Skill cause:** Safety Principles say "Amend `TECH.md` freely; GOAL is locked", and Step 1.3 (remediation) covers only `REVIEW.md` → `TECH.md`. A remediation after a human-gate ruling has no instruction to record the ruling, so a builder following the skill alone either leaves it in chat or edits a "locked" file without guidance.
- **Recommended fix:** in Step 1.3, add: when `REVIEW.md` records a human gate, append each ruling to `GOAL.md` Clarifications as a dated line naming the finding (append only; no R-ID text changes), in the remediation commit.
- **Confidence:** med · **Effort:** small

## F8 — The reviewer's conduct rule covers tracked files, not the processes its CLI drives start
`origin=hs-review:step-2 severity=medium category=tooling status=open target=.agents/factory/bin/temp_site.sh`
- **What happened:** the blind reviewer left two autoscaling `hsx` servers running after hand-back. Its deadline helper signalled a wrapper subshell rather than `hsx`, and `temp_site.sh` then removed the site out from under them. Each kept relaunching a client every period, which crashed at once. The harness refused the reviewer's own later `kill`, so a human has to stop them.
- **Skill cause:** Step 2 and the rubric's conduct rule require only a clean `git status`. `temp_site.sh` removes the site on exit but leaves running any background process the command started. The skill prescribes bounded CLI drives of long-running servers but gives no way to bound or reap them.
- **Recommended fix:** have `temp_site.sh` run the command in its own process group and signal the group (INT, then TERM) in its exit trap. Add to the conduct rule: no process you started may outlive your hand-back.
- **Confidence:** high · **Effort:** small

## F9 — CLI drives of launcher clusters need a host rewrite in the sandbox, and nothing says so
`origin=hs-review:step-2 severity=low category=missing-guidance status=open target=.agents/factory/review-rubric.md`
- **What happened:** the autoscaler hands each client `-H <host name>`, and the sandbox cannot resolve it. Every client exited with `Could not resolve host`. The orchestrator found this only after a 40 s drive came back as 11 failed launches; the reviewer found it independently. Both wrote a shim that points `-H` at loopback.
- **Skill cause:** the executed-evidence spine prescribes `--launcher` drives in `temp_site.sh` sites, and the factory docs are silent on this sandbox limit. Each review rediscovers it at the cost of a retry.
- **Recommended fix:** ship a recording launcher shim under `.agents/factory/bin/`. It logs argv and `HYPERSHELL_ZONE`, drops its own marker word, and rewrites `-H` to loopback. Point to it from the rubric's verification guidance.
- **Confidence:** med · **Effort:** small

## F10 — The clean route leaves the previous cycle's `blocked_reason` under an `approved` verdict
`origin=hs-review:step-4 severity=low category=instruction status=open target=.agents/skills/hs-review/SKILL.md`
- **What happened:** after cycle 1's changes-requested, Step 4's clean-route call (`--verdict approved --reviewed-commit … --touch`) left `review.blocked_reason` holding cycle 1's text. The orchestrator cleared it with a second call, `--blocked-reason ''`, by inference.
- **Skill cause:** the clean route names no `--blocked-reason`, and `set_phase.py` changes only the fields it is given. Any approval that follows a blocked cycle therefore carries a stale reason into `hs-publish`.
- **Recommended fix:** add `--blocked-reason ''` to the clean-route command in Step 4, or have `set_phase.py` clear `blocked_reason` on `--verdict approved`.
- **Confidence:** high · **Effort:** small
