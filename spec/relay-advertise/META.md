# META — `hs relay` names the address its site clients dial

> **Harness feedback log** for this feature — the producer artifact of the factory's self-improvement
> loop. Written by the lifecycle skills (`hs-feature` / `hs-plan` / `hs-build` / `hs-review`) when the
> **skillset itself** costs something; read by `hs-publish` (surfaced in the PR) and applied by
> `/hs-harness`. This file is **orthogonal** to the `GOAL → PLAN → TECH → REVIEW` spine — it is about
> the *toolchain*, not the feature — and is retained on merge like the rest of `spec/relay-advertise/`.
>
> **Silence is the default.** The bar for a finding is one test: *was this the **skill's** fault — not
> mine, not the task's?* A merely-hard task, a self-inflicted error, or a one-off content/code issue
> (that belongs in `GOAL.md` / `REVIEW.md`) is **not** a finding. The blind `hs-review` correctness
> reviewer never reads this file — it would leak author intent.

- **slug:** relay-advertise

## What worked well

Brief, optional reinforcement: a part of a skill / the harness that materially helped, so `/hs-harness`
knows what **not** to change. One line each, naming the skill/step. Skip the section entirely if
nothing stands out.

- hs-feature Step 4, *Promoting an issue*: the `target:` carry and the status/ROADMAP marker rule made acceptance into the cycle mechanical.

## Friction findings

Zero or more findings, appended below — each a markdown **section** so appending is a low-corruption
operation and a stdlib parser reads them (`uv run python .agents/factory/bin/meta_status.py
spec/relay-advertise/META.md`). Skills always write `status=open`; only `/hs-harness` flips it. `target` is a
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

## F1 — An `unshaped` seed's evidence is carried, not re-checked against develop
`origin=hs-feature:step4 severity=low category=missing-guidance status=open target=.agents/skills/hs-feature/SKILL.md`
- **What happened:** The seed was written against an older develop. Four of its `file:line` citations had drifted, its TLS claim held only for the system CA bundle (HyperShell's default `<auto>` cafile checks no host name), and its R3 asked for version markers the page already carries. The foreman's brief asked for verification; the skill alone would have had them carried into Problem as written.
- **Skill cause:** For `unshaped` the skill says "Carry the evidence (`file:line`, mechanism, …) into **Problem**"; only the `shaped` branch says to re-confirm against current `develop`. With "no big code reads" beside it, nothing tells the shaper that each cited line and claim must be re-read first.
- **Recommended fix:** In the `unshaped` bullet, add: re-read each cited `file:line` and claim on current `develop` before carrying it (a targeted read, not research), cite the current lines, and list any claim found false or drifted in the GOAL or the hand-off report.
- **Confidence:** med · **Effort:** small

## F2 — No factory helper bounds a verify step, and macOS has no `timeout`
`origin=hs-plan:step6 severity=low category=tooling status=open target=.agents/factory/bin/temp_site.sh`
- **What happened:** Every verify step had to be bounded (the foreman's standing rule), but the factory gives no way to do it: `timeout` and `gtimeout` are absent on the macOS hosts, so this plan wrote `spec/relay-advertise/drive/bound.sh` (a perl fork, SIGINT to the step's process group at the bound, SIGKILL 15 s later). Earlier cycles bounded steps ad hoc or not at all.
- **Skill cause:** Step 6 asks for "a real `verify:` command" and the TECH template's examples are unbounded; nothing in `.agents/factory/bin/` or the skill says how to bound a step that can hang (a relay, a server, a whole suite).
- **Recommended fix:** Move a bounding helper into `.agents/factory/bin/` (this cycle's `bound.sh` is a candidate) and have Step 6 and the TECH template say every long or process-starting `verify:` step runs under it with a stated bound.
- **Confidence:** med · **Effort:** small

## F3 — The tests-first red-list tooling lives in other cycles' spec folders, unnamed by the skill
`origin=hs-plan:step6 severity=low category=missing-guidance status=open target=.agents/skills/hs-plan/SKILL.md`
- **What happened:** The brief asked for a tests-first phase with a red list. The working pattern (a `red.txt`, `spec/relay-hardening/drive/red_check.sh` to prove each listed test fails on the base by an assertion or a named error, `spec/contended-writes/drive/rest_green.sh` for the rest, prototypes kept un-collected under `research/prototypes/`) had to be found by reading four earlier cycles' TECH files.
- **Skill cause:** Step 6 describes phases only as vertical slices; it names neither the tests-first phase several cycles use nor the shared scripts, which sit in other specs' `drive/` folders where a planner would not look.
- **Recommended fix:** Promote `red_check.sh`, its `red_outcome.py` plugin and `rest_green.sh` to `.agents/factory/bin/`, and add a Step 6 note: for a tests-first phase, write `red.txt`, run `red_check` on the base (prefer assertions; `raises=` only for the gap's own error), and keep prototypes named so the suite never collects them.
- **Confidence:** med · **Effort:** small
