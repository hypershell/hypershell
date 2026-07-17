# META — Harness refinements (external factory audit)

> **Provenance.** A full external audit of the software factory (`.agents/` + `spec/` dogfood
> artifacts), performed 2026-07-16 in an ad-hoc session, at the maintainer's request, against
> `develop` @ `ba4c49f`. Method: complete read of all six skills, the five `factory/` docs, five
> templates, four `bin/` scripts, and both settings files; the scripts were exercised empirically
> (round-trip idempotence on the real dogfood `TECH.md`, `meta_status.py` edge cases: fences,
> `· seen again`, status filters); the merged dogfood (`cli-cluster-restart-repeat-update`, PR #50)
> and git history served as ground truth. All `file:line` references are as of `ba4c49f`.
>
> **Purpose.** This file seeds the factory's self-improvement loop directly: findings use the
> standard `META.md` schema, enumerable via
> `uv run python .agents/factory/bin/meta_status.py spec/harness-refinements/META.md`.
> Schema extension: `origin=external-review:<area>` (these findings come from an audit, not a
> lifecycle-skill run). **No finding proposes loosening a non-negotiable gate — F1, F2, and F7
> strengthen gates** (relevant to `hs-harness` Safety §3).

- **slug:** harness-refinements
- **date:** 2026-07-16
- **verdict:** the factory is genuinely well designed and proven end-to-end by the dogfood, but two
  of its flagship guarantees (blind review, gated publish) have structural holes, and several stated
  principles are not yet backed by mechanism.

## What worked well

Verified strengths `hs-harness` must **not** regress while applying fixes:

- **Scripts-own-the-YAML is real:** `set_phase.py` round-trips the actual dogfood `TECH.md`
  idempotently (only `last_updated` changes); `validate()` refuses to write invalid frontmatter;
  `meta_status.py` handled every edge case thrown at it (fenced schema skipped, `· seen again`
  titles, multi-field bullet lines, status/severity/id filters).
- **The dogfood REVIEW.md is exceptional QA:** `EXPLAIN QUERY PLAN` evidence at 300k rows, the
  refutation protocol visibly applied, and the human gate fired exactly as designed when F1 (theirs)
  landed in `data/model.py`. Executed-evidence review caught a real partial-index bug that
  assertion-based review would have missed.
- **The self-improvement loop's asymmetry** (cheap observe / human-gated act, anti-thrash ledger,
  no meta-on-meta recursion, "a finding arguing to loosen a guardrail is itself a warning sign").
- **Honest epistemics inline:** "risk-reduction, not proof"; `hammerable: false` rejecting Shape
  Up's cut-quality assumption for correctness invariants.
- **Ceremony-scales-to-appetite has concrete numbers** (>~8–10 criteria, >~8 phases soft breakers) —
  the thing most 2026 spec-driven tooling lacks.

## Findings

Severity uses the `META.md` enum: `high` = a safety/gate/correctness gap in the factory's
guarantees; `medium` = a stated principle without a backing mechanism; `low` = polish/alignment.
F1–F2 high · F3–F8 medium · F9–F16 low. Per convention, `target` is a best-guess file with no line
number; evidence lines carry the audit's verified `file:line` refs (re-derive at apply time).

## F1 — Blind review isn't blind: the branch diff hands the reviewer PLAN/TECH/research
`origin=external-review:hs-review severity=high category=steering status=applied target=.agents/skills/hs-review/SKILL.md`
- **What happened:** the reviewer subagent is told not to read PLAN.md/TECH.md/research/META.md but is instructed to produce the diff with plain `git diff {base}...HEAD` — and those files are committed on the branch, so their full text arrives inside the diff.
- **Skill cause:** the curated-input design controls what the reviewer *reads* but not what the diff *contains*; blindness is defeated structurally, invisibly, on every review.
- **Recommended fix:** instruct the reviewer to diff with `git diff {base}...HEAD -- . ':(exclude)spec/'` (GOAL.md is already supplied inline); optionally, for mechanical rather than instructional blindness, spawn the reviewer in an isolated worktree with `spec/{slug}/{PLAN,TECH,research,META}*` deleted (Claude-Code-specific — add the portability.md fallback note). Also fixes cycle-2 contamination (prior REVIEW.md rides in the re-review diff) and cleans the scope-creep matrix (spec/ files otherwise appear as unmapped changes).
- **Evidence:** diff instruction at `.agents/skills/hs-review/SKILL.md:80`; squash commit `b27ee58` shows `PLAN.md` (258 lines) and `TECH.md` (373 lines) inside the reviewed range; the dogfood `REVIEW.md:4` claims the pass "does not see PLAN.md/TECH.md", contradicted structurally.
- **Confidence:** high · **Effort:** small

## F2 — `approved` is sticky: publish never checks for commits after the reviewed one
`origin=external-review:hs-publish severity=high category=instruction status=open target=.agents/skills/hs-publish/SKILL.md`
- **What happened:** `hs-publish` gates only on `review.verdict: approved`; `review.last_reviewed_commit` exists precisely to detect staleness and nothing consumes it, so any code commit after approval still publishes as approved.
- **Skill cause:** the gate checks a sticky flag, not the relationship between the approved state and HEAD.
- **Recommended fix:** publish pre-flight asserts `git diff {last_reviewed_commit}..HEAD -- . ':(exclude)spec/'` is empty, else STOP for re-review or explicit human override (the exclude correctly tolerates the review's own REVIEW.md/TECH.md commit and meta-note commits). Optionally `hs-build` resets the verdict to `none` whenever it commits code while verdict is `approved`.
- **Evidence:** gate at `.agents/skills/hs-publish/SKILL.md:63` reads only the verdict; `last_reviewed_commit` is written at `.agents/skills/hs-review/SKILL.md:103` and read nowhere.
- **Confidence:** high · **Effort:** small

## F3 — The factory's state substrate has zero tests
`origin=external-review:factory-bin severity=medium category=tooling status=open target=tests/test_factory_fsm.py`
- **What happened:** `_fsm.py`, `next_phase.py`, `set_phase.py`, and `meta_status.py` — the code that owns every feature's lifecycle state — have no test coverage; a grep of `tests/` finds only pytest's `tmpdir_factory`.
- **Skill cause:** the factory reproduced the exact anti-pattern its own constitution warns about ("`core/fsm.py` — load-bearing, untested — edit with care"); `hs-harness` Step 6's manual post-apply spot checks are the only net.
- **Recommended fix:** add ~15 unit tests (frontmatter round-trip and idempotence, `compute_next` ordering and drift warnings, `validate` rejections, meta-parser fence/filter/`· seen again` cases) as `tests/test_factory_fsm.py` + `tests/test_meta_status.py`, marked `unit`; they run free in existing CI. Note these files live outside `.agents/` — a deliberate, flagged extension of `hs-harness`'s remit (toolchain tests per repo convention), or route this one finding through a small lifecycle feature.
- **Evidence:** audit session effectively wrote several of these tests ad hoc (idempotence + parser edge cases) and they pass; no factory-related test files exist.
- **Confidence:** high · **Effort:** medium

## F4 — Circuit breakers run on session memory the factory explicitly distrusts
`origin=external-review:hs-build severity=medium category=tooling status=open target=.agents/factory/bin/set_phase.py`
- **What happened:** "fails its verify gate across repeated attempts" and "stuck `hill: uphill` across builds" have no durable counter — nothing records verify failures, so a phase that failed five times across five sessions looks fresh each time; REVIEW.md's "Cycle {n} of ≤3" likewise has no file-backed source, making the ≤2–3 review-cycle bound unenforceable.
- **Skill cause:** methodology principle 2 says files + git are the durable substrate, but the two circuit breakers were left on conversation memory.
- **Recommended fix:** add a per-phase `attempts:` counter (`set_phase.py --record-attempt`, bumped on a red verify gate) and auto-increment `review.cycle` when `--verdict` is set; wire hs-build's breaker prose and REVIEW.md's cycle header to those fields.
- **Evidence:** breaker prose at `.agents/skills/hs-build/SKILL.md:79-82`; substrate principle at `.agents/factory/methodology.md:44-47`; cycle header at `.agents/factory/templates/REVIEW.md:10`; no writer exists for either count.
- **Confidence:** high · **Effort:** medium

## F5 — The remediation path hand-edits YAML at the worst possible moment
`origin=external-review:hs-build severity=medium category=tooling status=open target=.agents/factory/bin/set_phase.py`
- **What happened:** `set_phase.py` can mutate statuses but cannot add a phase, so hs-build's remediation mode tells the model to "carefully append a new remediation phase" by hand-editing frontmatter YAML — while the FSM is blocked.
- **Skill cause:** the one mutation the scripts can't do is delegated back to in-context YAML editing, which `_fsm.py`'s own docstring names the primary FSM-corruption risk.
- **Recommended fix:** add `set_phase.py --add-phase` (id, name, satisfies, depends-on, verify, insert-after) so remediation phases go through the same validated, canonical serializer as every other mutation.
- **Evidence:** hand-edit instruction at `.agents/skills/hs-build/SKILL.md:101-103`; corruption-risk rationale at `.agents/factory/bin/_fsm.py:7-9`.
- **Confidence:** high · **Effort:** medium

## F6 — `verify:` CLI drives aren't hermetic: the blessed example hits the real database
`origin=external-review:templates severity=medium category=tooling status=open target=.agents/factory/bin/temp_site.sh`
- **What happened:** the factory correctly pushes CLI drives over tests, but its canonical example (`seq 100 | uv run hsx -t 'echo {}' -N4 && uv run hs list`) runs against the developer's real site/DB; there is no temp-site wrapper anywhere in the factory, and the blind reviewer's live cluster drives have the same exposure.
- **Skill cause:** the templates/rubric recommend a pattern the factory provides no safe tooling for; the dogfood dodged it only because all six phases' `verify:` happened to be pytest (which isolates via fixtures).
- **Recommended fix:** ship `.agents/factory/bin/temp_site.sh` (mktemp dir; export `HYPERSHELL_SITE`, `HYPERSHELL_DATABASE_FILE`; exec `"$@"`) and use it in every template/rubric example verify command; also protects post-condition assertions from stale cross-phase state.
- **Evidence:** raw examples at `.agents/factory/templates/TECH.md:29` and `.agents/factory/templates/REVIEW.md:17`; dogfood `TECH.md` P1–P6 verify commands are all pytest.
- **Confidence:** high · **Effort:** small

## F7 — GOAL "locked" is convention-only, and cycle-2 review semantics are undefined
`origin=external-review:hs-review severity=medium category=missing-guidance status=open target=.agents/skills/hs-review/SKILL.md`
- **What happened:** nothing detects a mid-build GOAL.md edit — a build that drifts the contract to match what got built then passes review, since the reviewer grades the drifted file; separately, the skill says "Write REVIEW.md from the template" (implying overwrite), while the dogfood correctly improvised appending a dated "Review cycle 2" section, and cycle 2 was human-verified remediation rather than a fresh blind pass — none of which is written down.
- **Skill cause:** the lock and the cycle semantics both live in prose with no mechanical check and no codified procedure.
- **Recommended fix:** review pre-flight runs `git log develop..HEAD --oneline -- spec/{slug}/GOAL.md`; more than the shaping commit → surface to the human ("contract changed mid-build — confirm before grading"). Codify what the dogfood did: cycle 2+ appends a dated section (never overwrites cycle 1), and state explicitly what a cycle-2 pass is (fresh blind re-review vs human-gated remediation verification).
- **Evidence:** lock language at `.agents/factory/templates/GOAL.md:6` and `.agents/skills/hs-build/SKILL.md:70-74`; overwrite instruction at `.agents/skills/hs-review/SKILL.md:96`; improvised append at `spec/cli-cluster-restart-repeat-update/REVIEW.md:144`.
- **Confidence:** high · **Effort:** small

## F8 — hs-harness's branch-and-PR flow contradicts demonstrated practice
`origin=external-review:hs-harness severity=medium category=instruction status=open target=.agents/skills/hs-harness/SKILL.md`
- **What happened:** all thirteen `[harness]` commits on develop are direct commits (no squash `(#NN)` suffixes; the maintainer's stated preference), yet the skill mandates a `harness/{slug}` branch + PR — the first real `/hs-harness` run will fight its owner's workflow.
- **Skill cause:** the skill encodes a ceremony its own maintainer doesn't use; additionally the "can read a still-open branch's META.md" path cannot work as written — a `harness/` branch off develop has no `spec/{slug}/META.md` to flip statuses in.
- **Recommended fix:** add a `direct` mode mirroring `hs-publish local` (commits straight to develop; consider making it the default) and declare pre-merge runs preview-only (`--dry-run` semantics) since status flips are impossible from a develop-based branch.
- **Evidence:** branch+PR mandate at `.agents/skills/hs-harness/SKILL.md:83-84` and Steps 5/8; direct-commit history `ba4c49f..871e081`; `harness-log.md` empty (the skill has never run).
- **Confidence:** high · **Effort:** small

## F9 — No terminal state: merged features stay `in_review` forever
`origin=external-review:hs-publish severity=low category=instruction status=open target=.agents/skills/hs-publish/SKILL.md`
- **What happened:** `TOP_STATUSES` includes `done` but nothing ever sets it; the merged dogfood's retained TECH.md reads `status: in_review`, verdict `approved`, permanently.
- **Skill cause:** the FSM defines a terminal state with no transition into it.
- **Recommended fix:** `hs-publish` flips `--top-status done` as its final pre-push branch commit, or the docs declare `in_review` + `approved` the intended terminal state of the retained record.
- **Evidence:** `.agents/factory/bin/_fsm.py:47`; `next_phase.py` output on `spec/cli-cluster-restart-repeat-update/TECH.md` post-merge.
- **Confidence:** high · **Effort:** small

## F10 — hs-feature's "Untracked GOAL.md" injection lists every tracked spec, and grows forever
`origin=external-review:hs-feature severity=low category=tooling status=open target=.agents/skills/hs-feature/SKILL.md`
- **What happened:** the "Untracked GOAL.md files" injection runs `ls spec/*/GOAL.md`, which lists every retained (tracked) spec — mislabeled noise that grows with each merged feature; separately, injected commands use `ls`/`head`/`tail`, which appear in no allowed-tools or settings allowlist, so a permission-blocked injection can silently blank the "Current state" block the pre-flights rely on.
- **Skill cause:** the injection command's fallback half contradicts its own label, and the injection allowlist audit was never done end-to-end.
- **Recommended fix:** keep only the `git ls-files --others --exclude-standard` half of that injection; audit every skill's injected sub-commands (`ls`, `head`, `tail`) against frontmatter allowed-tools + `.agents/settings.json` and add the missing safe-read entries.
- **Evidence:** `.agents/skills/hs-feature/SKILL.md:42`; `head`/`tail` in injections at `.agents/skills/hs-build/SKILL.md:39`, `.agents/skills/hs-review/SKILL.md:41`, `.agents/skills/hs-harness/SKILL.md:48`; no matching allow entries in `.agents/settings.json`.
- **Confidence:** high · **Effort:** small

## F11 — The rubric handed verbatim to the blind reviewer contains orchestrator-only instructions
`origin=external-review:hs-review severity=low category=steering status=open target=.agents/factory/review-rubric.md`
- **What happened:** the reviewer subagent receives `review-rubric.md` in full, including "Verdict & loop" instructions addressed to the orchestrator (write REVIEW.md, call ReportFindings, run set_phase.py) — inviting the subagent to perform them; nothing tells the reviewer to leave the tree clean, so an instrumenting reviewer can strand a dirty tree that STOPs the next lifecycle step.
- **Skill cause:** one document serves two roles without ownership annotations, and the reviewer prompt omits tree-hygiene requirements.
- **Recommended fix:** annotate rubric sections by owner (reviewer vs orchestrator) or pass the reviewer only its sections (scope, refutation, severity); add to the reviewer prompt: no edits to tracked files, revert any instrumentation, `git status --porcelain` must be clean on return.
- **Evidence:** orchestrator instructions at `.agents/factory/review-rubric.md:51-59`; handed in full per `.agents/skills/hs-review/SKILL.md:81`; benign in the dogfood but unguarded.
- **Confidence:** high · **Effort:** small

## F12 — hs-build's `skip review` argument reads as "skip /hs-review"
`origin=external-review:hs-build severity=low category=instruction status=open target=.agents/skills/hs-build/SKILL.md`
- **What happened:** the argument means "continue past the natural phase-boundary stop" but its name collides with the lifecycle's review stage.
- **Skill cause:** naming.
- **Recommended fix:** rename to `no-pause` (or `continuous`), keeping `skip review` as a deprecated alias for one cycle.
- **Evidence:** `.agents/skills/hs-build/SKILL.md:53`.
- **Confidence:** high · **Effort:** small

## F13 — getting-started.html duplicates factory truth with no drift check
`origin=external-review:factory-docs severity=low category=missing-guidance status=open target=.agents/skills/hs-harness/SKILL.md`
- **What happened:** the 56KB hand-built onboarding HTML restates the factory in a second medium and already needed same-day sync commits when the skillset changed.
- **Skill cause:** no step in the change workflow asks whether the onboarding page went stale.
- **Recommended fix:** add "did this change stale `getting-started.html`?" to hs-harness Step 6's post-apply checklist (or consciously document the page as point-in-time and dated).
- **Evidence:** sync commits `a8152e6`/`ba4c49f` (2026-07-16); no mention of the file in any skill's verification steps.
- **Confidence:** medium · **Effort:** small

## F14 — `git checkout *` in three skills permits silent working-tree discard
`origin=external-review:allowed-tools severity=low category=tooling status=open target=.agents/skills/hs-feature/SKILL.md`
- **What happened:** hs-feature, hs-publish, and hs-harness allow `Bash(git checkout *)`, which includes `git checkout -- .` (unprompted discard of the working tree); every actual use is covered by `git switch`. Also, `Bash(uv run *)` in every skill subsumes `uv run python -c '…'` (arbitrary code), so the tight git allowlist is accident-protection, not capability confinement.
- **Skill cause:** allowed-tools were enumerated for coverage, not minimality; the portability doc calls settings.json "the safe baseline" without stating the confinement caveat.
- **Recommended fix:** drop `Bash(git checkout *)` from all three frontmatters (keep `git switch`); add one honest sentence to `portability.md` that the allowlists guard against accidents, not adversarial capability.
- **Evidence:** frontmatters at `.agents/skills/hs-feature/SKILL.md:11`, `.agents/skills/hs-publish/SKILL.md:10`, `.agents/skills/hs-harness/SKILL.md:12`.
- **Confidence:** high · **Effort:** small

## F15 — CRLF-saved TECH.md reports "unterminated frontmatter"
`origin=external-review:factory-bin severity=low category=tooling status=open target=.agents/factory/bin/_fsm.py`
- **What happened:** the closing-fence match uses `rstrip("\n")`, so a `\r\n` line ending never equals `---`.
- **Skill cause:** one-character oversight in the fence scanner.
- **Recommended fix:** `rstrip("\r\n")` at the fence comparison (and cover it in the F3 test file).
- **Evidence:** `.agents/factory/bin/_fsm.py:81`.
- **Confidence:** high · **Effort:** small

## F16 — No aggregate status view across features
`origin=external-review:factory-bin severity=low category=tooling status=open target=.agents/factory/bin/next_phase.py`
- **What happened:** answering "where is everything" requires one `next_phase.py` invocation per slug; nothing walks `spec/*/TECH.md`.
- **Skill cause:** the tooling was built per-feature; the portfolio view was never needed until specs accumulated (they are retained on merge, so they will).
- **Recommended fix:** a `--all` mode (or tiny `factory_status.py`) emitting one line per spec: slug, top_status, verdict, next actionable phase. Pairs with F9 so merged work reads `done`.
- **Evidence:** usage contract at `.agents/factory/bin/next_phase.py:9-12` (single path only).
- **Confidence:** medium · **Effort:** small

## Watch item (not a finding)

The self-improvement loop is the newest, least-proven part of the factory: zero `META.md` files
existed before this one, `harness-log.md` has no entries, and `/hs-harness` has never run. Several
findings above (F1, F6, F7) are exactly the class of issue the loop should eventually catch on its
own — driving *this* batch is the loop's shakedown cruise, exercising the ledger, the status flips,
and the post-apply verification in one pass. Whether silence-by-default produces signal (not just
silence) over the next two or three features is the real test.

## Drive plan

Applied as direct-to-develop `[harness]` commits (the maintainer's demonstrated practice, and F8's
own recommendation), one atomic commit per finding with the status flip in the same commit, every
decision logged in `factory/harness-log.md`:

- **Pass 1 — gate integrity:** F1 F2 F11 (F1 + F11 edit the same review skill/rubric pair; F2
  completes the publish gate).
- **Pass 2 — mechanism debt:** F15 F4 F5 F16 F6 F3 (script work first, then the test file that
  covers it; F3's `tests/` files are a flagged extension of the `.agents/`-only remit).
- **Pass 3 — alignment & polish:** F7 F8 F9 F10 F12 F13 F14.

Alternative considered: shaping this as a regular feature (`/hs-feature`) for the full blind-review
treatment — rejected because it bends the "only hs-harness writes to `.agents/`" convention and
skips the loop's shakedown.
