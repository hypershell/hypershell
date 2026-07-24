# PLAN — Fix Zsh completion for `hsx` / `hs cluster`

> **Status:** Draft for review · **Last updated:** 2026-07-24
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md).

## 1. Summary

The real root cause is **not** the `(1)` exclusion form the GOAL suspected. The `--from-json`
option *description* contains an unescaped `]` — `[Read tasks from a JSON file ("FILE[@path]")]` —
and zsh's `_arguments` closes a `[...]` description at the **first unescaped `]`**. So the
description terminates early at `FILE[@path]`, the trailing `")]:json spec:_files` becomes garbage,
and `comparguments` rejects the whole spec (`invalid option definition`), aborting the *entire*
completion function. The fix is to escape the nested brackets: `("FILE\[@path\]")`. This was
confirmed by driving real zsh completion (see §6). Appetite is small: a 4-character escaping change
on two lines, plus a zsh-free regression test.

## 2. Design

Single hand-maintained asset: `share/zsh/site-functions/_hs` (pyproject maps it verbatim to
`share/zsh/site-functions/_hs` in the wheel — there is **no generator**). Two spec lines carry the
identical defect:

- `:562` `'(1)--from-json[Read tasks from a JSON file ("FILE[@path]")]:json spec:_files'` — the
  `_hs_cluster` function used by `hsx` / `hs cluster` (the reported symptom).
- `:465` `'(-f --task-file --from-json)--from-json[Read tasks from a JSON file ("FILE[@path]")]:json spec:_files'`
  — the `_hs_submit` function. **This context is broken too** (the GOAL's "submit works" premise is
  wrong — verified: `hs submit -<TAB>` raises the same error).

**Change:** in both descriptions, replace `("FILE[@path]")` with `("FILE\[@path\]")`. Nothing else
changes — option names, the `(1)`/`(--from-json)` exclusions, the positional `1:input file:_files`
(`:608`), and the `:json spec:_files` action are all left byte-identical. Once the description parses,
the `(1)` positional-exclusion works fine (it never was the problem) and `_files` generates file
candidates exactly as authored.

**Regression guard:** a new `@mark.unit` test `tests/test_completions.py` that statically lints
`share/zsh/site-functions/_hs` (no zsh/expect needed, so it is CI-portable): for every single-quoted
`_arguments` spec with a `[...]` description, the first *unescaped* `]` after the description-opening
`[` must be followed by `:` (an action), `'` (end of the quoted segment), or end-of-line. The buggy
lines fail this (`]` followed by `"`); the fixed lines pass. Validated against both the pre-fix and
post-fix file (catches exactly the 2 bad lines; zero false positives across the file, including the
`[[ … ]]` shell-test lines in helper functions).

No CLI behavior, help text, or `docs/_include/*.rst` snippet changes — this is a completion-asset bug
fix, not a CLI change. The bash completions carry no `FILE[@path]` string and are out of scope.

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1   | Escaping `:562` makes `_hs_cluster`'s `_arguments` parse → `hsx`/`hs cluster` completion runs with no `comparguments` error (verified via real-zsh harness). |
| R2   | The `:json spec:_files` action is unchanged; with the spec now valid, `--from-json <TAB>` offers files (verified: lists `alpha.json beta.json …`). |
| R3   | Only description text changes; every other spec (options, positional `1`, exclusions) is byte-identical → no lost candidates. |
| R4   | After the fix no spec uses a broken form: both the cluster `(1)` positional-exclusion and the submit grouped-option-exclusion are valid zsh; the "one broken form" the GOAL worried about was the bracket defect, now removed from **both** contexts. |

## 3. Invariant gate (AGENTS.md constitution check)

Checked against [`.agents/factory/invariants.md`](../../.agents/factory/invariants.md) before and
after design. Only **§12 (project conventions)** is touched:

- **§12 — `share/` completions kept in lockstep; wheel ships `share/`; CI asserts the paths.** The
  edit is in place, so the installed path `share/zsh/site-functions/_hs` is unchanged — the CI
  metadata job (which asserts *paths*, not content) stays green. No `docs/_include` update is due
  because CLI help text does not change.
- **§12 — tests tagged `@mark.unit`/`@mark.integration` under `--strict-markers`.** The new test is
  `@mark.unit`.

Sections §1–§11 (task lifecycle, `exit_status`, retries, server modes, FSM/threads, sentinels,
resources, signals, queue/TLS, config, cluster argv) are **not touched** — no Python runtime code
changes.

### Deviation justifications

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| Fix also edits `:465` (`hs submit`), beyond the GOAL's literal "cluster only" framing | Same root cause, identical defect; `hs submit -<TAB>` is verifiably broken | Fixing only `:562` leaves an identical known-broken completion in a sibling context — indefensible in review and contrary to R4's "no broken form remains" intent |

**GOAL correction (transparent, not silent drift):** the GOAL's *Problem* section attributes the bug
to the `(1)` exclusion vs the grouped form and states other contexts work. Research disproved both:
the cause is the unescaped bracket, and `hs submit` shares it. The R-IDs remain the contract and are
all satisfied by the correct fix; this correction is surfaced here and will be surfaced again at
`/hs-publish` for the human's review.

## 4. Rabbit holes (resolved)

- **What actually breaks the spec?** → Not `(1)`. Dropping the exclusion entirely still errored;
  escaping the description brackets fixed it. zsh closes a `[...]` description at the first
  *unescaped* `]`. Established with a real-zsh reproduction harness (`expect` + `compinit`), not
  theory.
- **Is `_hs` generated?** → No. `pyproject.toml` `[tool.hatch.build.targets.wheel.shared-data]`
  maps it verbatim; edit the file directly.
- **Can we guard this without zsh in CI?** → Yes. A static lint over the spec lines detects the
  premature-close pattern; prototyped against pre/post-fix files with correct results.

## 5. Risks & open questions

- **Interactive-only verification isn't in CI.** The definitive proof (real zsh completion) needs
  zsh + `expect`, which the maintainer has locally but CI does not. Mitigation: the CI gate is the
  zsh-free static lint; the real-zsh drive is run locally during build and recorded here.
- No open questions blocking build.

## 6. Verification strategy

- **Reproduce & confirm (local, real zsh):** drive completion in a pty against a candidate copy of
  `_hs` and assert the `comparguments … invalid option definition` line is gone for `hsx -`,
  `hs cluster -`, and `hs submit -`; and that `hsx --from-json <TAB>` lists `.json` files. (Harness
  used during planning: `expect` + `fpath=(<dir> $fpath); compinit -u`; the cached `hsx→_hs` name
  mapping plus fpath-prepend autoloads the candidate function body.)
- **Syntax gate:** `zsh -n share/zsh/site-functions/_hs` (passes).
- **Regression gate (CI-portable):** `uv run pytest -m unit -k completion` — the new static lint.

---

*Lean plan (small fix): no `research/` directory; findings inlined above.*
