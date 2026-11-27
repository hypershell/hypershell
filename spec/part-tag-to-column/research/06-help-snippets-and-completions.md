# 06 — Help snippets & completions: the §12 same-commit machinery

## Summary

`hs list` and `hs search` are **the same app** — `TaskSearchApp` (`src/hypershell/task.py:601`),
registered twice: as `'list'` in the top-level command dict (`src/hypershell/__init__.py:130`) and as
`'search'` under the `task` group (`src/hypershell/task.py:1235`). So there is **one** help-text source,
**one** docs include set, and **one** completion function per shell to touch — editing them covers both
subcommands.

The §12 same-commit rule (invariants.md:137–138) covers exactly two artifact families: the
`docs/_include/*.rst` help snippets and the `share/` shell completions. **Man pages are NOT part of the
same-commit rule** — they are regenerated at release time by `/hs-release`. This is confirmed by the
closest precedent, `961fb22 [feature] Add --all to task list`, whose entire file list was:
`docs/_include/task_search_desc.rst`, `docs/_include/task_search_help.rst`, `share/bash_completion.d/hs`,
`share/zsh/site-functions/_hs`, `src/hypershell/task.py` — **no man pages**.

## 1. `docs/_include/*.rst` — HAND-MAINTAINED (no generator)

There is **no generator** — no script under `.agents/`, `tools/`, no Makefile/noxfile/pyproject task, no
Sphinx argparse/autoprogram hook. The include files are hand-authored RST that mirror (but are not
byte-identical to) the plain-text help constants in `task.py`. The help text originates from string
constants:

- `SEARCH_PROGRAM = 'hs list'` — `task.py:552`
- `SEARCH_SYNOPSIS` — `task.py:553`
- `SEARCH_USAGE` (usage block) — `task.py:554`
- `SEARCH_HELP` (`SEARCH_USAGE` + Arguments/Options) — `task.py:568`, wired via
  `interface = Interface(SEARCH_PROGRAM, SEARCH_USAGE, SEARCH_HELP)` (`task.py:604`).

The **exact include files** for both `list` and `search` (there is **no** `list_*`/`task_list_*` file —
`hs list` reuses these):

- `docs/_include/task_search_usage.rst` — synopsis (RST backtick-wrapped)
- `docs/_include/task_search_desc.rst` — description prose
- `docs/_include/task_search_help.rst` — Arguments + Options reference

Consumers: `docs/cli/task_search.rst` (page titled "List", `.. include:: ../_include/task_search_*`) and
`docs/manual.rst:141–148`.

**Regen command: NONE — edit by hand** to match the updated `SEARCH_HELP` text. Add the new `--part`
option under the `Options` section of `task_search_help.rst`; update `task_search_usage.rst` **only if**
the synopsis line changes; touch `task_search_desc.rst` only if adding narrative (the `--all` commit did,
to explain the guard). Build/verify: `uv run sphinx-build docs docs/_build` (expect no new warnings; the
pre-existing `task_submit.rst`/`manual.rst` "not in any toctree" warnings are fine).

## 2. `share/` completions — one edit point per shell

Both shells route `list` and `search` to a **single** function, so `--part` is added once per shell.

### bash — `share/bash_completion.d/hs`, function `_hs_task_search` (defined at line 373)
Dispatch: `list) _hs_task_search ;;` (line 90) and `search) _hs_task_search ; return ;;` (line 259).
Options live in one whitespace-joined string, `all_opts` (lines 377–380):
```
	local all_opts="-h --help --all -w --where -t --with-tag -g --group -s --order-by --desc
	-F --failed -C --completed -S --succeeded -R --remaining -X --cancelled --retries --signal
	-f --format --csv --json -d --delimiter -l --limit -c --count
	-i --ignore-partitions --fields --tag-keys --tag-values"
```
**Add `--part`** to this string (e.g. append to the `-g --group` group or the last line). For value
completion, add a branch to the `case "${previous}"` block, mirroring `--signal`/`-l --limit`
(lines 425–432), e.g.:
```
		--part)
			COMPREPLY=($(compgen -W "0 1 2 3" -- "${current}"))
			return
			;;
```

### zsh — `share/zsh/site-functions/_hs`, function `_hs_list` (defined at line 400)
Dispatch: `list) _hs_list ;;` (line 179) and `search) _hs_list ;;` (line 332). Options are `_arguments`
lines. Model on the existing `--ignore-partitions` line (line 422):
```
        '(-i --ignore-partitions)'{-i,--ignore-partitions}'[Suppress auto-union feature (SQLite only)]' \
```
and `--signal` (line 415, shows value-completion syntax):
```
        '--signal[Match tasks killed by signal NAME]:signal:(HUP INT QUIT ABRT KILL TERM)' \
```
**Add** a line like `'--part[Filter by partition N]:partition:' \` alongside these (before the trailing
`'*:field:_hs_fields'`).

### hsx / hs cluster — NOT affected
`hsx`/`hs cluster` completions are separate functions (`_hs_cluster`); `list`/`search` do not touch them.
The bash `hsx` completion is a **symlink** `share/bash_completion.d/hsx -> hs`, and the wheel installs the
`hs` file under both names — so editing the single `hs` file covers both binaries.

## 3. Man pages — release-time regen, not this commit

`share/man/man1/{hs,hsx,hyper-shell}.1` are generated from `docs/manual.rst` via the `man_pages` table in
`docs/conf.py:153` (source doc `manual` → `hyper-shell.1` and `hs.1`; there is no `hsx` entry). Because
`docs/manual.rst:141–148` `.. include::`s the `task_search_*.rst` files, the new `--part` text **will**
flow into `hs.1` when the man pages are next rebuilt — the roff already contains the list/search help
(grep `hs.1` for "killed by signal" → 5 hits; note hyphens are roff-escaped as `\-`, so
"ignore-partitions" won't match literally).

Rebuild command (per `/hs-release` Step 4, `.agents/skills/hs-release/SKILL.md:157`):
`uv run sphinx-build -b man docs docs/_build/man`, then
`cp docs/_build/man/hs.1 share/man/man1/hs.1`, `cp docs/_build/man/hyper-shell.1 share/man/man1/hyper-shell.1`,
and `cp share/man/man1/hs.1 share/man/man1/hsx.1` (`hsx.1` is a byte-copy of `hs.1`).

**Do NOT rebuild man pages in the feature commit** — git history shows `share/man/man1/` is touched only
by `[release]`/`Rebuild manual pages` commits, never by feature commits. `/hs-release` owns this. (Minor
caveat: the release skill's Step-4 "verify only the `.TH` line changed" check assumes CLI content is
already current; after this feature it will legitimately also show the `--part` addition — expected, not
an error.)

## 4. CI metadata assertion — file LIST is unchanged, so it stays green

`.github/workflows/tests.yml:95–113` ("Assert completions & man pages ship in the wheel") checks a
**fixed** list of six wheel paths exists in `dist/*.whl`:
```
"share/bash-completion/completions/hs",
"share/bash-completion/completions/hsx",
"share/zsh/site-functions/_hs",
"share/man/man1/hs.1",
"share/man/man1/hsx.1",
"share/man/man1/hyper-shell.1",
```
The source→wheel remap lives in `pyproject.toml:119–125` (`[tool.hatch.build.targets.wheel.shared-data]`),
e.g. `"share/bash_completion.d/hs" = "share/bash-completion/completions/hs"`. Adding a `--part` option
changes only file **contents**, adds/removes **no** files, so both the shared-data map and the CI
assertion are **unaffected** — do not edit either. (Separately, `README.rst` must still pass
`twine check --strict`, but a CLI option does not touch it.)

## 5. Ordered same-commit checklist for adding `--part`

1. **`src/hypershell/task.py`** — add the `--part` argument to `TaskSearchApp.interface`
   (`interface.add_argument(...)`, ~line 672 near `--ignore-partitions`) and edit the `SEARCH_HELP`
   Options block (`task.py:568`); update `SEARCH_USAGE`/`SEARCH_SYNOPSIS` (`task.py:553–566`) **only if**
   the synopsis gains `--part`.
2. **`docs/_include/task_search_help.rst`** — hand-add a `--part` entry under `Options` mirroring the
   `--ignore-partitions`/`--signal` entries. Edit `task_search_usage.rst` iff the synopsis changed;
   `task_search_desc.rst` iff narrative is warranted.
3. **`share/bash_completion.d/hs`** — add `--part` to `_hs_task_search`'s `all_opts` (lines 377–380) and,
   for value completion, a `--part)` branch in the `case "${previous}"` block.
4. **`share/zsh/site-functions/_hs`** — add a `--part` `_arguments` line in `_hs_list` (near line 415/422).
5. **Do NOT touch** `share/man/man1/*.1` (release owns it) or the CI list / shared-data map.
6. **Verify:** `uv run hs list --help` and `uv run hs search --help` render the option;
   `uv run sphinx-build docs docs/_build` builds with no new warnings; optionally dry-run
   `uv run sphinx-build -b man docs docs/_build/man` to confirm it renders (but do not commit the `.1`
   output). Stage exactly the 5 files above (matching the `961fb22` precedent).
