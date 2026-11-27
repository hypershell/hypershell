# Brief 06 — Same-commit surface: docs snippets, shell completions, test plan

Scope: what must change **in the same commit** as the `--repeat`/`--update` (+ `--restart` doc
edits) CLI work (GOAL R18, invariants §12), and a concrete test plan driving the R4–R16 gate matrix.
Read-only investigation; all anchors verified against source.

## 1. Docs snippets (`docs/_include/*.rst`)

These are **hand-maintained RST mirrors** of the cmdkit `interface` help/usage strings — there is
**no generator script** (grep for `_include` in `docs/conf.py` finds nothing; snippets are literal
`.. include::` targets). The source-of-truth CLI strings live in the app `__doc__`/interface in
`src/hypershell/submit.py` and `src/hypershell/cluster/__init__.py:53-88`; the `test_*_help`/
`test_*_usage` tests compare `main(...)` stdout against `App.interface.help_text`/`usage_text`, so the
interface strings and these snippets must be edited together (snippets are not auto-checked, but drift
is a doc bug).

Files to touch for this feature:
- `docs/_include/submit_help.rst` — add `--repeat`, `--update` option blocks (Options section).
- `docs/_include/submit_usage.rst` — add `[--repeat | --update]` to the usage line.
- `docs/_include/submit_desc.rst` — describe re-submission gating (optional but recommended).
- `docs/_include/cluster_help.rst` — add `--repeat`/`--update`; revise `--restart` block (its
  "mutually exclusive to --forever" text and new interplay with `--update`).
- `docs/_include/cluster_usage.rst:1` — the `[FILE | --from-json SPEC | --restart | --forever]`
  group must gain `--repeat`/`--update`.
- `docs/_include/cluster_desc.rst` — the `--restart` narrative (mentions restart-where-left-off)
  should note the new detection/dedup semantics.

Consumers of these includes (no separate edits needed — they transclude): `docs/cli/submit.rst`,
`docs/cli/cluster.rst`, and `docs/manual.rst:64-94` (which also has abbreviated hand-written
usage lines `manual.rst:10-11,22` using `...` — likely fine to leave, but confirm).

Toctree caveat: `task_submit.rst`/`manual.rst` already emit pre-existing "not in any toctree"
warnings (AGENTS.md); adding option blocks introduces **no new** warnings. Build check:
`uv run sphinx-build docs docs/_build` and diff the warning set.

## 2. Man pages (also under `share/`, hand-maintained)

`share/man/man1/hs.1` and `share/man/man1/hsx.1` embed the **full help text** (both already carry
`--restart`/`--forever` blocks, e.g. `hsx.1:91,242,251`). Invariant §12 / AGENTS.md scopes the
same-commit rule to `docs/_include` + `share/` completions, but the man pages are `share/` files that
mirror the same help — update them for `--repeat`/`--update` for consistency. CI asserts only that
they *ship*, not their content, so this is a completeness item, not a gate.

## 3. Shell completions (`share/`)

- **bash** — `share/bash_completion.d/hs`:
  - `_hs_submit` `all_opts` string at **`:518-519`** — add `--repeat --update`.
  - `_hs_cluster` `all_opts` string at **`:654-661`** — add `--repeat --update` (this same function
    backs `hsx`). No new value-completion `case` arms needed (both are boolean flags).
- **zsh** — `share/zsh/site-functions/_hs`:
  - `_hs_submit` `_arguments` block at **`:461-484`** — add `'--repeat[...]'` and
    `'(--repeat)--update[...]'` / `'(--update)--repeat[...]'` mutual-exclusion pair (mirror how
    `--no-tls` uses `(--no-tls)` exclusion groups).
  - `_hs_cluster` `_arguments` block starting **`:552`** (after `--forever`/`--restart` at
    `:573-574`) — add the same two flags with appropriate exclusions.

## 4. CI metadata job (keep in lockstep — but no change needed here)

`.github/workflows/tests.yml:95-112` ("Assert completions & man pages ship in the wheel") checks the
wheel `namelist` contains the six shared-data paths (`share/bash-completion/completions/hs`+`hsx`,
`share/zsh/site-functions/_hs`, three man pages). The repo→wheel mapping is
`pyproject.toml:118-124` (`[tool.hatch.build.targets.wheel.shared-data]`; repo path
`share/bash_completion.d/hs` maps to wheel `share/bash-completion/completions/hs`, and the same
source file is mapped twice to install under both `hs` and `hsx`). **This feature adds no new
`share/` files**, so the CI list and the pyproject mapping stay as-is — only edit the file *contents*.
(Flag only if a new completion file is introduced.)

## 5. Test infrastructure (verified)

- `tests/conftest.py`: `isolate_environment()` at import + autouse `clean_env` blanks
  `HYPERSHELL_*`, sets `HYPERSHELL_CONFIG_FILE=''`, binds `HYPERSHELL_SERVER_PORT` to `free_port()`.
  `temp_site` fixture sets `HYPERSHELL_SITE`, `HYPERSHELL_DATABASE_FILE`, `LOGGING_LEVEL=DEBUG` — use
  it for anything touching the DB (which is everything here).
- `tests/__init__.py`: `main(argv)→(rc,out,err)`, `main_lines`, `assert_output(pat,out,count=)`
  (regex line-count over stderr logs, DEBUG-level), `create_taskfile(temp_site, lines)`,
  `create_taskfile_echo(temp_site, count, tags=)`, `create_json_taskfile`, `NO_OUTPUT`,
  `UUID_PATTERN`, `exit_status` from `cmdkit.app`.
- Established patterns to copy: `test_cluster.py:152` (`test_cluster_database` — DB assertions via
  `hs list --count` and `hs list <field> -f plain -s submit_time`) and `:200` (`test_cluster_backfill`
  — pre-`hs submit` then `hsx --restart`, count checks). `test_submit.py` mixed-input/refusal
  patterns (`main_lines(...) == (exit_status.bad_argument, NO_OUTPUT, ['CRITICAL ...'])`).
- **Markers: only `@mark.unit` / `@mark.integration`** (`--strict-markers`); use `@mark.parametrize`.
  `client.py` has no dedicated test file — gating logic lives in `submit.py`/`data/model.py`, so
  coverage should target `test_submit.py`, a new `test_source.py`/`test_restart.py`, and
  `test_cluster.py`. `test_initdb.py` exists for the fresh-initdb non-goal.

## 6. Test plan (unit vs integration, per requirement)

**Unit** (`@mark.unit`, import `Task`/`SubmitApp` directly, no subprocess) — new `test_source.py`:
- R2: identity fingerprint is order-independent over tags, stable across template change, and
  excludes uuid/attempt/timing/exit_status. Assert two tasks (args+group+tags equal, different
  template/uuid) hash equal; reordered tags hash equal; different args/group/tags hash differ.
- R1/R4: source record md5 + task-count computed from file content (recompute helper == expected).

**Integration** (`@mark.integration`, `temp_site`, shell out) — extend `test_submit.py` +
new `test_restart.py`; each seeds `create_taskfile_echo`:
- R3 exempt: `hs submit 'echo x'` twice → both succeed (source `<direct>`); stdin (`seq 4 | hs
  submit -f -`) twice → both succeed.
- R5: submit file, submit again no flag → non-zero, `assert_output` refusal naming prior source.
- R6: submit, rewrite file (same path, changed content), submit no flag → refuse, message suggests
  `--update`.
- R7: incomplete-prior warning — submit file, remove/cancel some rows (or interrupt), re-detect →
  count-warning log (mechanism TBD — see open question).
- R8: `hs submit <file> --repeat` after identical prior → `hs list --count` doubles.
- R9: `hs submit <file> --update` after edited file → only novel-identity tasks added (count == old
  + new-unique); unchanged lines skipped.
- R10: `hs submit <file> --update --repeat` → non-zero contradictory.
- R11: `hsx <file>` (no flag) on seen file → refuse (same as R5).
- R12: `hsx <file> --restart` same fingerprint → submits only not-present, completes; run twice →
  second is idempotent (nothing to do, halts). Changed fingerprint → refuse, suggest `--update`.
- R13: `hsx <file> --update` (no `--restart`) → non-zero ambiguous.
- R14: `hsx <file> --update --restart` after edit → new source, only novel tasks added + run.
- R15: `hsx <file> --repeat` → all tasks resubmitted (count grows).
- R16: `hsx <file> --update --repeat` → non-zero contradictory.
- R18: throughout, `assert_output` on ingest/detection logs (found N / existed M / submitting K /
  refusal reason). Logs are DEBUG in `temp_site`, matched against stderr.
- non-goal (fresh initdb): extend `test_initdb.py` — after `hs initdb` the source table/indices
  exist; `hs list --count` == 0.

## RECOMMENDATION

Same-commit edit set:
- **Docs:** `docs/_include/{submit_help,submit_usage,submit_desc,cluster_help,cluster_usage,cluster_desc}.rst`
  — kept in lockstep with the interface strings in `submit.py` + `cluster/__init__.py`.
- **Completions:** `share/bash_completion.d/hs` (`_hs_submit` `all_opts` `:518`, `_hs_cluster`
  `all_opts` `:654`) and `share/zsh/site-functions/_hs` (`_hs_submit` `:461`, `_hs_cluster` `:552`).
- **Man pages (completeness):** `share/man/man1/hs.1`, `share/man/man1/hsx.1`.
- **CI:** `.github/workflows/tests.yml:95-112` and `pyproject.toml:118-124` need **no change** (no
  new `share/` files).
- **Tests:** unit `test_source.py` (R1/R2/R4); integration additions to `test_submit.py`,
  `test_cluster.py`, new `test_restart.py`, `test_initdb.py` per the R-mapped list above.

`verify:` commands to seed TECH.md phases (run in a `temp_site`, `uv sync` first):
- `uv run hs initdb`
- `printf 'echo a\necho b\n' > f.in; uv run hs submit -f f.in` (expect success) `; uv run hs submit
  -f f.in` (expect refuse) `; uv run hs list --count`
- `uv run hs submit -f f.in --repeat; uv run hs list --count` (doubled)
- edit `f.in`; `uv run hs submit -f f.in --update; uv run hs list --count` (only novel added)
- `uv run hsx f.in --restart` twice (second idempotent); `uv run hsx f.in --update` (ambiguous fail);
  `uv run hs submit -f f.in --update --repeat` (contradictory fail)
- doc/completion sanity: `uv run sphinx-build docs docs/_build` (no new warnings);
  `bash -n share/bash_completion.d/hs`; `zsh -n share/zsh/site-functions/_hs`.

## Open questions
- R7 count-warning: how to construct an "incomplete prior submission" deterministically in an
  integration test (cancel rows? kill mid-run? directly delete)? Depends on the source→count storage
  chosen in PLAN — coordinate with the model/schema brief.
- Do the hand-written abbreviated usage lines in `docs/manual.rst:10-11,22` need the new flags, or do
  their `...` ellipses suffice? (Recommend leaving; confirm at plan time.)
- Whether a new `test_restart.py` is preferred vs folding cluster gating into `test_cluster.py`
  (stylistic; both fit existing conventions).
