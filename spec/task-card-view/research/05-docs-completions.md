# 05 — Docs & completions checklist for adding `card` to `-f/--format`

Scope: add a new `card` value to `hs info`, `hs list`/`hs search` (`hs list` and `hs search`
are the **same** app — `TaskSearchApp`; `__init__.py:127,130` map both to it). `hs wait --info`
is **adjacent** (mirrors `info`, shares `(normal, json, yaml)`) — flagged below but out of the
stated feature scope.

Line numbers are current-tree snapshots; re-verify before editing.

---

## A. In-source help strings + argparse choices (source of truth) — `src/hypershell/task.py`

These are the truth the `docs/_include/*.rst` snippets and man pages mirror. The **`choices`
list is load-bearing** (argparse rejects `card` unless added); the help string is cosmetic.

- **`hs info` (TaskInfoApp)**
  - `task.py:141` — help text: `  -f, --format     FORMAT   Format task info ([normal], json, yaml).` → add `card`.
  - `task.py:172` — `output_formats: List[str] = ['normal', 'json', 'yaml']` → **add `'card'`** (choices).
  - (Synopsis `task.py:127` shows only `[-f FORMAT]`; no enumeration to edit.)
- **`hs list`/`hs search` (TaskSearchApp)**
  - `task.py:591` — help: `  -f, --format      FORMAT   Format output (normal, plain, table, csv, json).` → add `card`.
  - `task.py:663` — `output_formats: List[str] = ['normal', 'plain', 'table', 'json', 'csv']` → **add `'card'`** (choices).
  - Impl notes (out of docs scope but required for the feature to work): formatter dispatch is
    `getattr(self, f'print_{self.output_format}')` (`task.py:745`) → needs a `print_card` method;
    `check_output_format()` (`task.py:821+`) may need a `card` branch. Not a docs/completion edit.
- **`hs wait --info` (TaskWaitApp) — ADJACENT, out of scope**
  - `task.py:310` help + `task.py:339` `output_formats = ['normal','json','yaml']`. `wait` delegates to
    `TaskInfoApp(output_format=self.output_format)` (`task.py:361`), so **`hs wait --info -f card`
    would be rejected** unless this list also gains `'card'`. Decide during planning; not required
    by the stated scope.

---

## B. docs/_include/*.rst help snippets (same-commit per AGENTS.md §Config/Docs)

- **`docs/_include/task_info_help.rst:11`** — `Format task info ([normal], json, yaml).` → add `card`.
- **`docs/_include/task_search_help.rst:75`** — ``Specify output format (either ``normal``, ``plain``, ``table``, ``csv``, ``json``).`` → add ``card``.
  - Surrounding prose `task_search_help.rst:77–79` explains when to use each format — optionally add a
    sentence for `card` (not strictly an enumeration line).
- **`docs/_include/task_wait_help.rst:19`** — ADJACENT: `Format task info ([normal], json, yaml).`
  (edit only if `wait` gains `card` per A above).

---

## C. Other docs

- No hand-written format enumerations exist outside `_include` and the generated man pages.
  `docs/manual.rst` only `.. include::`s the `_include/*.rst` snippets, so editing B propagates.
- `docs/_build/**` is generated Sphinx output — **do not edit**.

---

## D. bash completion — `share/bash_completion.d/hs`

`share/bash_completion.d/hsx` is a **symlink → hs**, so editing `hs` covers both. Values are a
space-separated `compgen -W "..."` word list — just add `card`.

- **`share/bash_completion.d/hs:297`** — `_hs_task_info`: `compgen -W "normal json yaml"` → add `card`.
- **`share/bash_completion.d/hs:418`** — `_hs_task_search` (list/search): `compgen -W "normal plain table csv json"` → add `card`.
- **`share/bash_completion.d/hs:327`** — ADJACENT: `_hs_task_wait` `compgen -W "normal json yaml"`
  (edit only if `wait` gains `card`).
- (The search opts blob at `hs:377–380` lists flags, not format values — no change.)

---

## E. zsh completion — `share/zsh/site-functions/_hs`

Spec syntax: `'(<mutex flags>)'{-f,--format}'[<desc>]:format:(<space-separated values>)'`.
Add `card` inside the trailing `(...)`.

- **`_hs:355`** — info: `'(-f --format --json --yaml)'{-f,--format}'[Format task info]:format:(normal json yaml)'` → `(normal json yaml card)`.
- **`_hs:417`** — search/list: `'(-f --format --json --csv)'{-f,--format}'[Format output]:format:(normal plain table csv json)'` → `(normal plain table csv json card)`.
- **`_hs:377`** — ADJACENT: wait `:format:(normal json yaml)` (edit only if `wait` gains `card`).

---

## F. man pages — GENERATED (not hand-maintained for content)

Man pages are built by Sphinx from `docs/manual.rst` (→ the `_include` snippets), per the
`hs-release` skill (`.agents/skills/hs-release/SKILL.md:157–165`):

```
uv run sphinx-build -b man docs docs/_build/man
cp docs/_build/man/hs.1          share/man/man1/hs.1
cp docs/_build/man/hyper-shell.1 share/man/man1/hyper-shell.1
cp share/man/man1/hs.1           share/man/man1/hsx.1     # hsx.1 is a copy of hs.1
```

`hs.1` and `hsx.1` are byte-identical (25042→115608B); `hyper-shell.1` differs only by program
name. Current enumeration lines (identical across all three): **info = line 1228**, **wait =
line 1289**, **search = line 1436**.

**Recommendation:** after editing the `_include` snippets (B), **regenerate** the three man pages
with the command above rather than hand-patching. Version is unchanged, so the diff should be
exactly the format-enumeration lines (no `.TH` change). Regenerating in the same commit keeps
`share/man` consistent with the snippets and prevents a surprise non-version man diff at the next
`/hs-release` (Step 4 verifies the man diff is version-only). Hand-patching lines 1228/1436 in all
3 files is possible but fragile — prefer regeneration.

---

## G. CI metadata guard — only ADDING values, no new files

`.github/workflows/tests.yml:96–113` ("Assert completions & man pages ship in the wheel") asserts
these **wheel** paths exist:
`share/bash-completion/completions/{hs,hsx}`, `share/zsh/site-functions/_hs`,
`share/man/man1/{hs,hsx,hyper-shell}.1`. Those are the *installed* paths; `pyproject.toml:119–125`
(`[tool.hatch.build.targets.wheel.shared-data]`) remaps the on-disk `share/bash_completion.d/{hs,hsx}`
→ `share/bash-completion/completions/{hs,hsx}`. **All edits above modify existing files only** (bash
`hsx` is a symlink to `hs`), so the asserted path list is **unaffected** — no new files, no CI list
change.

---

## Edit summary (minimum required for stated scope: info + list/search)

| File | Line(s) | Change |
|------|---------|--------|
| `src/hypershell/task.py` | 141, 172 (info); 591, 663 (search) | help text + **choices** `+card` |
| `docs/_include/task_info_help.rst` | 11 | help `+card` |
| `docs/_include/task_search_help.rst` | 75 (+77–79 prose optional) | help `+card` |
| `share/bash_completion.d/hs` | 297 (info), 418 (search) | word list `+card` (hsx symlink covered) |
| `share/zsh/site-functions/_hs` | 355 (info), 417 (search) | `:format:(...)` `+card` |
| `share/man/man1/{hs,hsx,hyper-shell}.1` | 1228 (info), 1436 (search) | **regenerate** via sphinx-build -b man |

Adjacent (decide in planning, NOT in stated scope): `wait` at `task.py:310/339`,
`task_wait_help.rst:19`, `bash hs:327`, `zsh _hs:377`, man line 1289.
