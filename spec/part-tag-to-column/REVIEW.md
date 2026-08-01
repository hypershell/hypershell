# REVIEW — Promote `part` from a bookkeeping tag to a first-class column

> Adversarial QA by `hs-review`, run via a blind fresh-subagent correctness pass. The reviewer graded
> the branch diff against [`GOAL.md`](GOAL.md) + the AGENTS.md invariants **only** — it did not see
> `PLAN.md`/`TECH.md`/`research/`/`META.md`. Every finding cites an **executed** command. Findings were
> sanity-checked against source by the orchestrator before this record was written.

- **Reviewed commit:** a3af539 (`a3af53941279b9e0d79d81cdb522b1274f04933a`)  ·  **Base:** develop  ·  **Date:** 2026-07-31
- **Verdict:** changes-requested
- **Cycle:** 1 of ≤3 — mirrors `review.cycle` in `TECH.md`

## Verification run

Commands actually executed (blind reviewer, then orchestrator confirmation):

- `uv run pytest -q` → **422 passed** (~245s). No failures.
- `uv run pytest -q tests/test_source.py tests/test_initdb.py tests/test_list.py` → **31 passed**.
- `uv run sphinx-build -E -b html docs docs/_build` → build succeeded, **2 warnings** (both the
  pre-existing baseline `task_submit.rst`/`manual.rst` toctree warnings; no new warnings).
- CLI drive in a throwaway site (`.agents/factory/bin/temp_site.sh`): submit 4 → complete 2 →
  `hs initdb --rotate --yes`. Confirmed completed rows land in `local.1` with `part=1`; incomplete
  stay in main at `part=0`; `part` never appears in tag JSON in either file
  (`json_extract(tag,'$.part')` = NULL on all rows); auto-union count 4, `--ignore-partitions` count 2;
  `--part 1`→partition rows, `--part 0`→main, `--part 99`→empty; `--part` composes with `-t`;
  `hs list part`, `hs info -x part`, `--json`/`--csv`/table all expose the column; `hs task search --part`
  identical to `hs list --part`.
- Orchestrator source confirmation of F1: `NORMAL_MODE_TEMPLATE` (`task.py:1294-1319`) has no `{part}`
  placeholder while rendering every other `Task.columns` member; `check_output_format` (`task.py:821-828`)
  routes bare `hs list`/`hs info` to that template.

## Requirement → evidence matrix

| R-ID | Implemented by | Verified how | Status |
|------|----------------|--------------|--------|
| R1 | `data/model.py:301` — `part: Mapped[int] = mapped_column(INTEGER, nullable=False, default=0)`, positioned after `fingerprint` (:299), immediately before `tag` (:303) | Read source; schema test | ✅ |
| R2 | `data/model.py:390` — `tag = {**(tag or {}), **inline_tags}` (the `{'part':0}` injection removed) | `test_new_keeps_part_out_of_tag_and_identity`; CLI: `json_extract(tag,'$.part')`=NULL on all rows | ✅ |
| R3 | Plain `INTEGER`/`Integer()` column type (`model.py:96,301`); `--part` is a plain column filter | Read source (no SQLite-only construct) | ✅ |
| R4 | `data/__init__.py:139/147/157` use `Task.part`; `json_set`/`json_extract`/`type_coerce` for part removed (import dropped) | `test_rotate`; CLI rotate drive; `grep` confirms no `json_*`-for-part remains | ✅ |
| R5 | `compute_fingerprint` hashes `dict(tags or {})` with no part special-casing | `test_new_keeps_part_out_of_tag_and_identity` (fingerprint stable); retry path copies parent fingerprint | ✅ |
| R6 | `create_all` schema includes `part` | `test_fresh_schema_creates_source_table_columns_and_indices` asserts `part` in task columns; CLI submit on fresh DB → `part=0` | ✅ |
| R7 | Suite green; docs build clean; `docs/_include/initdb_desc.rst` updated (part→column); new behavior + `--part` covered by tests | pytest 422; docs build; see F2 (man-page artifact lag, non-blocking) | ✅ (see F2) |
| R8 | `'part': int` in `Task.columns` (`model.py:337`); selectable by name / `--fields` / `--order-by` / table / plain / json / csv / `-x` | CLI: `hs list part`, `hs info -x part`, `--json` all show it — **but** the detailed `NORMAL_MODE_TEMPLATE` (`hs info`, bare `hs list`) omits it | ❌ partial (F1) |
| R9 | `--part N` on `TaskSearchApp` (`task.py:654`); filter in `SearchableMixin.__build_filters` (`task.py:534-535`); `docs/_include/task_search_{usage,help}.rst` + bash & zsh completions updated same commit | `test_part_filter`; CLI drive on both `hs list` and `hs task search`; `grep` share/ completions | ✅ |

Unmapped changes (possible scope creep): **none**. Every hunk maps to an R-ID (the `test_initdb.py`
helper swap and `sqlite3` import serve the R7 rotation test; the inert `SearchableMixin.part_filter`
attribute is shared R9 infrastructure — `TaskUpdateApp` exposes no `--part`, so `hs update` is unchanged).

## Findings

### [HIGH/CONFIRMED] R8 partially unmet — `part` omitted from the detailed task view (`hs info`, bare `hs list`)
- **Where:** `src/hypershell/task.py:1294-1319` (`NORMAL_MODE_TEMPLATE`)
- **Failure scenario:** a user runs `hs info <id>` (or bare `hs list`, which `check_output_format` routes
  to the same normal template) to see which partition a task landed in after a rotate → the value is
  queried and present in `task.to_dict()` but the template has no `{part}` placeholder, so it is silently
  dropped. `part` is the **only** `Task.columns` member the template omits — it renders `group`,
  `fingerprint`, `source`, `previous_id`/`next_id`, all hosts, `waited`, `duration`, etc.
- **Evidence:** blind CLI drive — full `hs info`/`hs list --all` output shows 24 field lines, none for
  `part`; `hs info -x part` → `0` proves the data is present but unrendered. Orchestrator source
  confirmation: template lines 1294-1319 contain `group: {group}` (line 1296) but no `part` line;
  `Task.columns` (`model.py:337`) does contain `part`.
- **Touches requirement:** R8 — "available wherever other columns are (e.g. … `hs info`), **consistent
  with columns like `group`**." `group` appears in the detailed view; `part` does not.
- **Not a coupled-core finding:** `task.py` is a leaf app (per AGENTS.md), so this does **not** trigger
  the mandatory high-blast-radius human gate.
- **Suggested remediation:** add a `part: {part}` line to `NORMAL_MODE_TEMPLATE` (natural home: adjacent
  to `group`, or immediately before `tags`), and cover it with a test asserting the normal/`hs info`
  output includes `part`.

### [LOW/PLAUSIBLE — non-blocking] Man-page artifact still describes `part` as a tag
- **Where:** `share/man/man1/hs.1:1181` (and identical `hsx.1`, `hyper-shell.1`): "applies a special
  purpose `part:N` tag."
- **Why non-blocking:** the man pages are **Sphinx-generated artifacts** (`docs/conf.py:153 man_pages`;
  `/hs-release` rebuilds them via `cp docs/_build/man/hs.1 …`). Their source, `docs/_include/initdb_desc.rst`,
  **was correctly updated** this branch (now: "recording that partition's index in each moved task's
  `part` column"). The committed `.1` files are dated Jul 23 (the 2.9.0a1 release) and are simply stale;
  the next `/hs-release` regeneration will pick up the corrected source. The §12 same-commit rule scopes
  to `docs/_include/*.rst` + `share/` completions (both correctly updated), not to release-time man pages.
- **Evidence:** `grep -n part share/man/man1/hs.1` → line 1181 old text; `docs/_include/initdb_desc.rst:7-8`
  → new column-based text. Surfaced for human awareness; does **not** auto-loop.

## Human-gate triggers

- **None.** The one CONFIRMED finding (F1) is in `task.py` (a leaf app), not the high-blast-radius core,
  and touches no security/DB-lifecycle invariant. The `data/model.py` and `data/__init__.py` changes
  themselves reviewed clean (lifecycle predicates, retry chain, CANCEL_STATUS filters, fingerprint all
  intact). No mandatory human sign-off is required to proceed with the loop.

## Optional completeness sub-pass

- Not run this cycle (plain `/hs-review`; no `completeness` argument). The requirement→evidence matrix
  above already maps every phase's R-IDs to executed evidence.
