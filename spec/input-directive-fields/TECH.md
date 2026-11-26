---
slug: input-directive-fields
title: Task input sets every declarable field, with KEY=VALUE or KEY:VALUE
kind: fix
appetite: big
status: blocked
branch: fix/input-directive-fields
base: develop
current_phase: done
last_updated: '2026-11-26'
phases:
- id: P1
  name: 'Tests first: the red list on 8dcbe126 (tests and spec only)'
  status: done
  satisfies:
  - R1
  - R2
  - R3
  - R4
  - R5
  - R6
  - R7
  - R8
  - R9
  depends_on: []
  parallel: false
  hammerable: false
  hill: uphill
  verify: test -z "$(git diff --name-only 8dcbe126 -- . ':!spec' ':!tests')" && test
    -n "$HS_TEST_POSTGRES" && RED_BASE_REV=8dcbe126 RED_LIST="$PWD/spec/input-directive-fields/red.txt"
    sh spec/update-client-retry/drive/bound.sh 3600 sh spec/relay-hardening/drive/red_check.sh
    && RED_LIST="$PWD/spec/input-directive-fields/red.txt" sh spec/update-client-retry/drive/bound.sh
    2400 sh spec/contended-writes/drive/rest_green.sh tests/test_input_fields.py tests/test_input_zones.py
    tests/test_update_tags.py tests/test_bounded_tags.py tests/test_group_integer.py
    tests/test_zone_dispatch.py
- id: P2
  name: The shared reader in core/tag.py; every tag checked; hs update on it (R4,
    R5, R8, R9)
  status: done
  satisfies:
  - R4
  - R5
  - R8
  - R9
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: uphill
  verify: sh spec/update-client-retry/drive/bound.sh 3600 uv run pytest -q -p no:cacheprovider
    tests/test_update_tags.py tests/test_update_kinds.py tests/test_update_numbers.py
    tests/test_update_datetime.py tests/test_update_refusals.py tests/test_update_group.py
    tests/test_update.py tests/test_data/test_model.py && sh spec/update-client-retry/drive/bound.sh
    1200 uv run pytest -q -p no:cacheprovider tests/test_input_fields.py -k 'naked
    or every_tag'
- id: P3
  name: Task input reads the declarable fields with either separator (R1 inline and
    -t, R2, R3, R4, R5, R6)
  status: done
  satisfies:
  - R1
  - R2
  - R3
  - R4
  - R5
  - R6
  depends_on:
  - P2
  parallel: false
  hammerable: false
  hill: uphill
  verify: sh spec/update-client-retry/drive/bound.sh 3600 uv run pytest -q -p no:cacheprovider
    tests/test_input_fields.py tests/test_bounded_tags.py tests/test_group_integer.py
    tests/test_group_stream.py tests/test_submit.py tests/test_submit_json.py tests/test_data/test_model.py
    && sh spec/update-client-retry/drive/bound.sh 600 sh spec/input-directive-fields/drive/idf_drive.sh
    fields
- id: P4
  name: 'Zones from input: registration, JSON, no database, the cluster warning (R1,
    R6, R7)'
  status: done
  satisfies:
  - R1
  - R6
  - R7
  depends_on:
  - P3
  parallel: false
  hammerable: false
  hill: uphill
  verify: test -n "$HS_TEST_POSTGRES" && sh spec/update-client-retry/drive/bound.sh
    3600 uv run pytest -q -p no:cacheprovider tests/test_input_zones.py tests/test_input_fields.py
    tests/test_zone_dispatch.py tests/test_zone_model.py tests/test_zone_display.py
    tests/test_submit_json.py && for m in zones nodb; do sh spec/update-client-retry/drive/bound.sh
    900 sh spec/input-directive-fields/drive/idf_drive.sh $m || exit 1; done
- id: P5
  name: Docs, help and the constitution (R10)
  status: done
  satisfies:
  - R10
  depends_on:
  - P4
  parallel: false
  hammerable: false
  hill: uphill
  verify: grep -q 'KEY=VALUE' docs/_include/submit_desc.rst && grep -q 'zone' docs/_include/submit_desc.rst
    && ! grep -rn 'hs task ' docs/_include docs/cli && grep -q 'declarable' AGENTS.md
    && sh spec/update-client-retry/drive/bound.sh 1200 uv run pytest -q -p no:cacheprovider
    tests/test_bounded_docs.py tests/test_input_fields.py -k 'doc' && UV_PROJECT_ENVIRONMENT="${TMPDIR:-/tmp}/docs-venv-idf"
    sh spec/update-client-retry/drive/bound.sh 900 uv run --python 3.12 --group docs
    sphinx-build -W --keep-going -q -E -b html docs "${TMPDIR:-/tmp}/docs-html-idf"
- id: P6
  name: 'Gate: the whole suite serially, PostgreSQL, the red list, every drive mode,
    the docs'
  status: done
  satisfies:
  - R1
  - R2
  - R3
  - R4
  - R5
  - R6
  - R7
  - R8
  - R9
  - R10
  depends_on:
  - P5
  parallel: false
  hammerable: false
  hill: uphill
  verify: test -n "$HS_TEST_POSTGRES" && env -u HS_TEST_POSTGRES sh spec/update-client-retry/drive/bound.sh
    5400 uv run pytest -q -p no:cacheprovider && sh spec/update-client-retry/drive/bound.sh
    3600 uv run pytest -q -p no:cacheprovider tests/test_input_fields.py tests/test_input_zones.py
    tests/test_update_tags.py tests/test_group_integer.py tests/test_zone_dispatch.py
    && RED_BASE_REV=8dcbe126 RED_LIST="$PWD/spec/input-directive-fields/red.txt" sh
    spec/update-client-retry/drive/bound.sh 3600 sh spec/relay-hardening/drive/red_check.sh
    && for m in fields zones nodb; do sh spec/update-client-retry/drive/bound.sh 900
    sh spec/input-directive-fields/drive/idf_drive.sh $m || exit 1; done
review:
  last_reviewed_commit: 25627e9a
  verdict: changes-requested
  blocked_reason: 'docs: none/bare-field refusal missing from cluster, server, run
    markers (review cycle 2)'
  cycle: 2
---
# TECH.md — Task input sets every declarable field, with `KEY=VALUE` or `KEY:VALUE`

Resume ground-truth: `uv run python .agents/factory/bin/next_phase.py spec/input-directive-fields/TECH.md`.

- **Contract:** [`GOAL.md`](GOAL.md). **Design:** [`PLAN.md`](PLAN.md). **Research:** [`research/`](research/).

## Conventions (apply to every phase)

- [`AGENTS.md`](../../AGENTS.md) is the constitution; checklist
  [`invariants.md`](../../.agents/factory/invariants.md) §1, §11 (zones), §12. `data/model.py` is high
  blast radius.
- One phase per `hs-build` invocation; one atomic signed commit (code + `TECH.md` state), subject
  `[fix] Build input-directive-fields P<n>: …`; **no `Co-Authored-By` trailer**.
- **Tests first.** P1 lands every new test, red on `8dcbe126`, in `red.txt`; later phases change a test
  only to fix a mistake in it, noted in the commit body and in `red.txt`.
- Docs and help name top-level commands only; no internal or cluster names; no key on a command line.
  Tests and drives write task files or split arguments, never `hs submit "cmd # HYPERSHELL: …"` as one
  argument (PLAN §5, `quote_arg`).
- pytest runs serially; PostgreSQL cases need `HS_TEST_POSTGRES` exported, the sandbox off. Docs builds
  need the sandbox off (`sphinx_sitemap`). `UV_CACHE_DIR` per run under `$TMPDIR`.
- Keep every name tests import from `hypershell.task` (`near_columns`, `check_tag`, `change_value`,
  `COLUMN_KINDS`, `read_changes`) importable there (R8).

---

## Phase P1 — Tests first
**Satisfies:** R1–R9 · **Depends on:** —

- [x] `tests/test_input_fields.py` (new): per input form (file inline, tag-only line, stdin, `-t`,
      `hs run -t`) and both separators — the five fields set from `KEY=VALUE` and `KEY:VALUE` (R3);
      precedence (R2); `grop=2` refused naming `group` and the `grop:2` hint, `grop:2` a tag (R4);
      `exit_status=0` refused, `exit_status:0` a tag (R5); refusal forms: whole input exit 5 naming the
      line, a stream refuses that line only, `-t` exit 2, `hs run -t` exit 2 (R6); `naked other:a/b`
      refused in `-t` and inline (R9); `cores=none`/naked `cores` refused; `cores:4.0` still 4 (Q1).
- [x] `tests/test_input_zones.py` (new): `zone:NAME`/`zone=NAME` inline, `-t`, JSON string key set the
      zone column with no `zone` tag and register the zone (R1), on `db_site` (SQLite and PostgreSQL);
      inline over `-t` over `--zone` (R2); JSON non-string zone refused (record N, exit 5); a zone name
      with `/` accepted; `zone:none` is a zone named none, `zone:default` the default zone; a stream into
      `hs server` registers its zones; R7 refusals for `--queue`, `hs cluster --no-db`, `hs server
      --no-db` (file, stream line, JSON, `-t`); the cluster warning (Q2).
- [x] `spec/input-directive-fields/red.txt`: one node per red test, `pg:` for the PostgreSQL ones, a
      header naming the base `8dcbe126` and the count; guards (green on base) are not listed.
- [x] Nothing outside `tests/` and `spec/` changes.

## Phase P2 — The shared reader
**Satisfies:** R4, R5, R8, R9 · **Depends on:** P1

- [x] `core/tag.py`: `TagError`, `split_token`, `edit_distance`, `near_names`, `read_token`, `check_tag`
      (PLAN §2.1).
- [x] `data/model.py`: `Task.ensure_valid_tag` loops over `check_tag` (every tag, R9).
- [x] `task.py`: `read_changes`/`read_assignment` on the reader; `TagError` → `ArgumentError`; names kept.

## Phase P3 — Task input reads the declarable fields
**Satisfies:** R1 (inline, `-t`), R2, R3, R4, R5, R6 · **Depends on:** P2

- [x] `model.py`: `DECLARABLE`, `FIELD_READERS`, `read_input_tokens`, `TaskTokenError`; `split_argline`
      on it; `Task.new` pops the five first, then checks the rest; `zone` → `zone_id`; `tag_text` retired.
- [x] `submit.py`: `read_tag_options`/`check_tags` on `read_input_tokens` (exit 2).
- [x] `task.py`: `TaskRunApp` checks `-t` before `ensuredb` (exit 2).
- [x] `spec/input-directive-fields/drive/idf_drive.sh` (new, SPDX header): mode `fields`.

## Phase P4 — Zones from input
**Satisfies:** R1, R6, R7 · **Depends on:** P3

- [x] Registration: `DatabaseCommitter` records each input zone before the first task in it, on the
      writing thread, for every Loader path with a database (whole input and streams alike; the Loader
      hands it `zone_names`, id → name); `hs run`, `submit_one` and the one-task submit before the add.
      *Amended:* the scan does not collect zones and `SubmitApp.run`/`prepare_source` do not ensure
      them — the committer covers those paths too, so registration has one home.
- [x] JSON `zone` key (string only) in `validate_json_records` (`json_record_tags` carries it to
      `Task.new`, which pops it, unchanged).
- [x] R7: the `zones=False` flag on scans and `LiveSubmitThread` Loaders; `--queue` `-t` refusal.
- [x] `hs cluster`: one WARNING per input zone its clients do not serve (`GatedSource.serves`; none
      under `--autoscaling`, which launches clients for every zone).
- [x] `idf_drive.sh`: modes `zones` and `nodb`.

## Phase P5 — Docs, help and the constitution
**Satisfies:** R10 · **Depends on:** P4

- [x] PLAN §2.4 pages and help strings, with `versionchanged:: 2.9.0` markers (no `:pr:` role).
- [x] `AGENTS.md` and `invariants.md`: the declarable fields from task input (PLAN §3), in lockstep.
- [x] Man pages rebuilt (version unchanged).
- [x] *Amended:* `tests/test_input_fields.py` gains three docs tests (`test_docs_describe_both_separators`,
      `test_docs_tag_option_takes_fields`, `test_docs_mark_the_change`), since no test there matched the
      verify's `-k 'doc'`; they are new guards, not in `red.txt`.

## Phase P6 — Gate
**Satisfies:** R1–R10 · **Depends on:** P5

- [x] Run the frontmatter verify (sandbox off, `HS_TEST_POSTGRES` exported). Record counts and time.
      2026-11-26, on the develop merge 9978da5e: whole suite 5,861 passed, 443 skipped (45 min);
      PostgreSQL-aware files 443 passed (6 min); red list 191 of 191 red on 8dcbe126; drive modes
      `fields`, `zones`, `nodb` all ok; docs `-W` exit 0. Total 58 min.
