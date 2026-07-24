# REVIEW — Fix Zsh completion for `hsx` / `hs cluster`

> Adversarial QA by `hs-review`, run with a blind correctness pass delegated to a fresh subagent. The
> correctness pass grades the branch diff against [`GOAL.md`](GOAL.md) + the AGENTS.md invariants
> **only** — it does not see `PLAN.md`/`TECH.md` (avoids grading-its-own-homework / plan-sycophancy).
> Every finding cites an **executed** command, not an assertion.

- **Reviewed commit:** bc38f4276c9a088ef77b5919a2aea27413bce60d  ·  **Base:** develop  ·  **Date:** 2026-07-24
- **Verdict:** approved
- **Cycle:** 1 of ≤3 — mirrors `review.cycle` in `TECH.md`

## Verification run

Commands actually executed and their outcomes (the spine of the review):

- `zsh -n share/zsh/site-functions/_hs` → exit 0 (syntax OK), on both base and HEAD.
- `uv run pytest -m unit -k completion` → **4 passed**, 411 deselected (orchestrator re-ran independently; reviewer subagent likewise saw 4 passed).
- Real zsh 5.9 completion harness (zpty-driven `compinit -u`, working-tree `share/zsh/site-functions`
  prepended to `fpath`):
  - **Pre-fix (develop):** `hsx -<TAB>`, `hs cluster -<TAB>`, **and** `hs submit -<TAB>` all emit the
    `invalid option definition` error with `opts=0` — no candidates offered.
  - **Post-fix (HEAD):** all three run clean — `hsx`/`hs cluster` offer 49 options, `hs submit` offers
    23; no `comparguments` error.
  - `hsx --from-json <TAB>` in a dir with `tasks.json data.json other.txt` → lists exactly
    `data.json  other.txt  tasks.json` (via `_files`), no error.
- `git status --porcelain` → empty at reviewer hand-back and after orchestrator sanity check (clean tree).

## Requirement → evidence matrix

Bidirectional traceability. Flag requirements with no implementing change **and** changes that map to
no requirement (scope creep).

| R-ID | Implemented by (file/commit) | Verified how | Status |
|------|------------------------------|--------------|--------|
| R1 — completion runs with no `comparguments` error | `share/zsh/site-functions/_hs:562` (bc38f42) | Real zsh harness: pre-fix all three contexts errored w/ 0 opts; post-fix all clean | ✅ |
| R2 — `--from-json` offers file candidates in cluster | `share/zsh/site-functions/_hs:562` (bc38f42) | `hsx --from-json <TAB>` listed the `.json`/other files via `_files`, no error | ✅ |
| R3 — no loss of other cluster options / positional | `_hs:562` unchanged elsewhere (bc38f42) | Full 49-option menu rendered post-fix (vs 0 pre-fix); positional `:608` untouched; `hsx <TAB>` still offers files | ✅ |
| R4 — self-consistent `--from-json` spec form | `_hs:465` + `_hs:562` both escaped (bc38f42) | Both specs now use `("FILE\[@path\]")`; description renders as literal `Read tasks from a JSON file ("FILE[@path]")` | ✅ |

Unmapped changes (possible scope creep): **none.** The fix also escapes the **submit** spec at
`_hs:465`. The GOAL frames submit as already-working, but the reviewer confirmed by execution that the
pre-fix submit context was **also** broken by the identical unescaped `("FILE[@path]")` — the real root
cause is the unescaped nested `]`, independent of the `(1)` vs `(-f --task-file --from-json)` exclusion
prefix the GOAL fingered. Touching `:465` is therefore correct and necessary for **R4** (self-consistency)
and repairs a second real breakage; it maps cleanly to R4/R1 intent, not scope creep. This root-cause
correction was recorded transparently by `hs-plan` (per TECH.md) and is not a fresh GOAL contradiction.
`tests/test_completions.py` maps to the regression-guard intent of R1/R4 (tagged `@mark.unit`).

## Findings

**None.** The blind reviewer actively tried to break the fix with a real zsh 5.9 completion harness and
adversarial inputs to the test linter; everything held. The diff is a two-spec bracket-escaping change
to a shipped completion asset plus a regression test — **zero `src/` changes**, so no runtime/behavioral
blast radius and no high-blast-radius core file touched.

Non-blocking note (not raised as a finding — cannot manifest against the guarded asset): the linter
`iter_bad_arg_specs` (`tests/test_completions.py:38`) has two theoretical edge cases that do not fire on
`_hs` — (a) it could false-positive on a non-spec line starting with `'` that contains `[...]` (none
exist), and (b) it could false-negative on a malformed spec not starting with `'` (impossible — every
spec here is single-quoted). It correctly flags the defect class and accepts paren-containing / escaped
/ multi-`]` descriptions. LOW at most; the file lints clean today.

## §12 project conventions

Clean: completion-only change with no CLI surface change (so no `docs/_include/*.rst` update is due),
bash completions untouched (explicit non-goal; bash uses no `[...]` description syntax), new tests tagged
`@mark.unit` under `--strict-markers`, no version hardcoding.

## Human-gate triggers

None. No CONFIRMED finding, and no high-blast-radius core file (`data/model.py`, `server.py`,
`client.py`, `core/queue|tls|fsm|thread|signal.py`, `cluster/remote|ssh.py`) or security/DB-lifecycle
invariant is touched. The diff is confined to `share/zsh/site-functions/_hs` and a new test file.

## Optional completeness sub-pass (separate reviewer; may see TECH.md)

Not run (single-phase, small-appetite fix). The single planned phase P1 is marked `done` and its
`verify:` gate (`uv run pytest -m unit -k completion`) passes; scope did not balloon (2 asset lines + 1
test file, matching the appetite).
