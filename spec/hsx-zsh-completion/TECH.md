---
slug: hsx-zsh-completion
title: "Fix Zsh completion for hsx / hs cluster"
kind: fix
appetite: small
status: in_progress
branch: fix/hsx-zsh-completion
base: develop
current_phase: P1
last_updated: "2026-07-24"
phases:
  - id: P1
    name: "Escape --from-json description brackets + add completion regression test"
    status: pending
    satisfies: [R1, R2, R3, R4]
    depends_on: []
    parallel: false
    hammerable: false
    hill: crest
    verify: "uv run pytest -m unit -k completion"
review:
  last_reviewed_commit: ""
  verdict: none
  blocked_reason: ""
  cycle: 0
---

# TECH.md — Fix Zsh completion for `hsx` / `hs cluster`

The **context engine and finite-state machine** for building this fix. The YAML frontmatter is the
resume ground-truth (`uv run python .agents/factory/bin/next_phase.py spec/hsx-zsh-completion/TECH.md`);
the per-phase checklist below is the work.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md) — R-IDs are the contract.
- **Authoritative design:** [`PLAN.md`](PLAN.md).

## Conventions (apply to every phase)

- Commit conventions, code style, and load-bearing invariants come from [`AGENTS.md`](../../AGENTS.md);
  the curated footgun list is [`.agents/factory/invariants.md`](../../.agents/factory/invariants.md).
- One atomic commit containing **both** the code and the `TECH.md` state change. Subject:
  `[fix] Build hsx-zsh-completion P1: …`. **No `Co-Authored-By` trailer.**
- This is a completion-asset bug fix with no CLI/help-text change, so no `docs/_include/*.rst` update
  is due. Only `share/zsh/site-functions/_hs` (path unchanged) and a new test are touched.

---

## Phase P1 — Escape `--from-json` description brackets + add completion regression test
**Satisfies:** R1, R2, R3, R4 · **Depends on:** —
**Goal:** `hsx` / `hs cluster` (and `hs submit`) Zsh completion runs with no `comparguments` error,
`--from-json` still completes files, and a CI-portable test prevents the defect class from returning.

- [ ] In `share/zsh/site-functions/_hs`, edit **both** `--from-json` spec lines (`_hs_submit` ~`:465`
      and `_hs_cluster` ~`:562`): replace `("FILE[@path]")` with `("FILE\[@path\]")`. Change nothing
      else — option names, the `(1)` / `(--from-json)` exclusions, the positional `1:input file:_files`,
      and the `:json spec:_files` action stay byte-identical.
- [ ] Confirm shell syntax: `zsh -n share/zsh/site-functions/_hs`.
- [ ] Drive **real zsh completion** and record the result (needs `zsh` + `expect`, present locally):
      assert the `invalid option definition` line is gone for `hsx -`, `hs cluster -`, and
      `hs submit -`, and that `hsx --from-json <TAB>` lists `.json` files. (Prepend the working-tree
      `share/zsh/site-functions` to `fpath`, `compinit -u`, then TAB.)
- [ ] Add `tests/test_completions.py` (`@mark.unit`, SPDX header, `REPO = Path(__file__).parent.parent`
      style à la `tests/test_meta_status.py`): a static lint of `share/zsh/site-functions/_hs`. For
      every stripped line starting with `'` that contains `[`, take the first `[` as the description
      open and scan for the first *unescaped* `]`; assert the following char is `:`, `'`, or
      end-of-line. Assert zero offending lines (guards the whole file, incl. `--from-json`).
- **Verify:** `uv run pytest -m unit -k completion`.
- **Touches:** `share/zsh/site-functions/_hs`, `tests/test_completions.py`.

---

## How `hs-build` drives this

1. `next_phase.py` prints the next actionable phase.
2. Pre-flight: clean tree, on `fix/hsx-zsh-completion`, `develop` reachable.
3. Execute every `[ ]` in P1 (consult `PLAN.md` for detail).
4. Run the `verify:` command — never advance on a checkbox alone.
5. Mark P1 `done`, `--touch`; one `[fix]` commit; stop and report. STOP and escalate only on a
   `GOAL.md` contradiction (note: the PLAN already records the transparent GOAL root-cause correction —
   that is *not* a fresh contradiction to escalate).
