# PLAN — `hs update` writes every requested change to exactly the rows it counts, contended or not, and says plainly when it cannot

> **Status:** Draft for review · **Last updated:** 2026-11-15
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). Backing detail is in
> [`research/`](research/00-digest.md). Every design element traces to a GOAL R-ID.

## 1. Summary

Every write `hs update` makes moves into `Task` classmethods on the per-row re-run helper the server's
writes already use (`Task.__write_rows`), extended with the user's filter as the row condition, a
SET expression for tags, a DELETE form, and an abort at the bound. Each `hs update` builds its filter
once, as self-contained criteria, and its change once, as one SET (fields plus one tag expression),
so combined changes land together (R5). The legacy form writes its one row on that helper (R1).
`--limit` writes its selected ids one row at a time, each re-checking the filter, in one transaction
(R2). Without `--limit` the update stays one set-based statement; on PostgreSQL it returns the ids it
wrote, and one residual read finds the rows it missed, which go through the per-row helper (R2). Any
row still not written at the bound rolls the whole update back and raises `Task.Contended`, which the
app reports and exits 5 on (R4). The counts printed are the rows written, with a WARNING when lower
than the count (R3). Tests come first, in a commit CI shows red; docs, AGENTS.md and invariants §1
change with the code (R6, R7).

## 2. Design

### 2.1 The filter: `SearchableMixin.filter_criteria()` (`src/hypershell/task.py`) — R2

A cached method returns the filter as a tuple of self-contained Core criteria: the `-w` clauses (and
the alias flags `-F/-S/-C/-R/-X`, `--signal`, `-g`, `--retries`) through `WhereClause.compile()` as
today, then the tag criteria. Built once (today's `__build_filters` appends to `where_clauses` on every
call, research/02 §Q4). The SQLite name-only tag criterion becomes
`func.json_extract(Task.tag, literal('$."name"')).isnot(None)`, one bind per tag, instead of a text
clause whose value lives on the Query (`query.params(key=…)`), which a per-row statement cannot carry.
`build_query()` filters on the same criteria, so `hs list` and `hs update`'s count, selection and writes
share one filter. Side effect: on SQLite, `-t a b` (two name-only tags) checks both, where today it
checks only the last (research/02 §Q4a); PostgreSQL is unchanged. **Foreman decision D2.**

### 2.2 The change: one SET (`src/hypershell/data/model.py`) — R5

`Task.__change(values, tags, drop, dialect) -> Dict[str, ColumnElement | value]`: the field values as
given, `--cancel`'s three columns and `--revert`'s `dict.fromkeys(Task.revert_columns)` (the app's
inlined list of thirteen columns goes), and, when tags are set or removed, one `tag` expression over
the row's own tag (research/02 §Q3):

- SQLite: `json_remove(json_set(task.tag, '$.k0', v0, …), '$.r0', …)`, each part omitted when empty,
  values as `literal()` binds — byte for byte today's filtered-form expressions, merged.
- PostgreSQL: `(task.tag || CAST(:patch AS jsonb)) - CAST(:names AS text[])` (two binds whatever the
  number of keys; PG 9.5/10 operators, inside the PG 13 floor).

Sets apply before removals, as today. `dialect` is the session bind's (`Session.get_bind().dialect.name`),
never the import-time `DATABASE_DIALECT`, so in-process tests on a rebound session build the right
SQL. The same SET serves the set-based statement and every per-row write, so `--limit`'s tag merge
happens in the statement at write time, not from the selected snapshot. Consequence on SQLite for
`--limit` (today an ORM merge): a boolean tag value is stored `1`/`0` and a dotted key nests, as the
filtered form already does; `-t KEY:true` matches either. **Foreman decision D3.**

### 2.3 `Task.__write_rows` extensions (`data/model.py`) — R1, R2, R4

Additions, each defaulting to today's behaviour so the server's callers are untouched:

- **`where: Tuple[ColumnElement, ...] = ()`** — condition `'filter'`: the UPDATE matches `id` and the
  criteria; the lookup applies the same and reads `id`. No row found: gone or no longer matching,
  skipped at DEBUG (`Ignored {path} for task ({id}) - task gone or no longer matches`), never raised on,
  not returned; a row found is re-run. Statements with `where` are built per call (criteria differ per
  invocation), not through the cached `__row_statements`.
- **SET expressions**: `values` entries may be SQL expressions (the tag expression), used as given.
- **`delete: bool = False`** — the write is `DELETE FROM task WHERE <match>`; same count, lookup and
  re-run.
- **`abort: bool = False`** — at the bound, after today's WARNING (`Contended {path} write did not land
  for task ({id}) after N attempts - {consequence}`), roll the transaction back (the caller's earlier
  writes with it) and raise `Task.Contended(id)`. `commit=False` (from #109) lets the caller hold one
  transaction across calls.

`Task.Contended` (subclass of a new `model.Contended(DatabaseError)`, beside `NotFound`) carries the
message `Update of task ({id}) was contended after N attempts - nothing was written`.

### 2.4 The legacy form: `Task.update(id, **changes)` — R1

`Task.update` overrides `Entity.update` (whose `bulk_update_mappings` turns a contended 0-row match
into `StaleDataError`): `__write_rows([id], changes, 'update', 'nothing written', abort=True,
prefix='u_')`, no condition. A missing row raises `StaleDataError` as today, which `update_legacy`
maps to `Task.NotFound` (exit 5, "not found"); a contended row that exists is re-run and written
(exit 0); one not written at the bound raises `Task.Contended` (exit 5). The tag read-modify-write
(`Task.from_id(uuid).tag` merged in Python) stays (non-goal); `from_id` already raises `NotFound` for a
missing row. `Entity.update_all`/`update`/`delete_all` stay for `Client`; no `Task` caller remains.

### 2.5 `--limit`: `Task.update_selected` / `Task.delete_selected` — R2, R3, R4, R5

The app selects ids only (`select(Task.id)` with the criteria, `--order-by`, `--limit`), after the prompt
as today, then calls `Task.update_selected(ids, criteria, change) -> int` (or `delete_selected(ids,
criteria) -> int`): one `__write_rows(ids, change, 'update', …, where=criteria, abort=True, commit=False,
prefix='u_')` (or `delete=True`), then one commit; returns the number written. A selected row that no
longer matches at its write is skipped and not counted (`hs update --limit 10 -R --cancel` no longer
cancels a task that completed meanwhile); a contended one is re-run; one not written at the bound rolls
back every row of every former batch (`UPDATE_BATCH_SIZE` goes). The shared-iterator defect goes with the
batches: one SET carries every change. Lock cost: research/01 §3 (every written row stays locked to the
commit; SQLite holds its write lock for the whole update, about 13 µs per row locally). Uncontended
cost: one UPDATE per row, no lookup (pinned).

### 2.6 Without `--limit`: `Task.update_matching` / `Task.delete_matching` — R2, R3, R4, R5

`Task.update_matching(criteria, change) -> int`, one transaction:

1. `UPDATE task SET <change> WHERE <criteria>` — on PostgreSQL with `RETURNING id`; written = its rows
   (SQLite: its rowcount).
2. PostgreSQL only: the residual `SELECT id FROM task WHERE <criteria>`, minus the returned ids
   (client-side): rows missed under contention, or that came to match meanwhile (written and counted,
   gate ruling). `xmin` was the leaner test but the `timescale` provider's columnstore chunks refuse it
   (research/01 §2). SQLite skips the residual: its write transaction excludes every other writer, so
   its count is exact.
3. The residual ids through `__write_rows(…, where=criteria, abort=True, commit=False)`; their count
   added.
4. Commit; return the total.

`Task.delete_matching(criteria) -> int` is the same with `DELETE … WHERE <criteria>`; its PostgreSQL
residual is the filter alone (a deleted row is gone for its own transaction), so no `RETURNING`.

Uncontended cost: SQLite one UPDATE; PostgreSQL one UPDATE and one SELECT, whatever the number of rows
(pinned). The PostgreSQL UPDATE path holds the written ids in memory and reads back every written row
still in the filter (none when the change moves rows out of it; all of them for a tag change that does
not): about 100 bytes per id. The alternative without that cost, a temporary table of written ids,
needs the TEMP privilege; left as an option (§5).

### 2.7 `TaskUpdateApp` (`src/hypershell/task.py`) — R1, R3, R4

- `exceptions` maps `Task.Contended` to `handle_exception` (status `runtime_error`, 5), so it is logged
  (CRITICAL `Contended: Update of task (…) was contended … - nothing was written`, after the helper's
  WARNING) with no traceback file. `update_legacy` keeps mapping `StaleDataError` to `Task.NotFound`.
- `update_tasks`: criteria and change built once; the count, prompt, 0-count stop and `--cancel`'s
  "already scheduled" warning as today; then `written = Task.delete_matching / delete_selected /
  update_matching / update_selected(...)`; if `written < count`, WARNING `{count - written} of the
  {count} tasks counted no longer matched and were left as they are`; then INFO `Deleted {written}
  tasks` / `Updated {written} tasks`; exit 0. `DATABASE_DIALECT` branches leave `update_tasks`.
- An update with nothing to change (no assignment, tag, `--remove-tag`, `--cancel`, `--revert` or
  `--delete`, e.g. `hs update -f -w group==0`) today logs "Updated N tasks" and writes nothing. It
  writes nothing and logs `Updated 0 tasks`, without the "no longer matched" warning, which would be
  false. **Foreman decision D4.**

### 2.8 Tests (R6) — all tagged; red where §6's table says

- **SQLite fault injection** (`tests/test_contended_recovery.py`): `DriverContention` gains the kinds it
  may lose (default `('mark', 'cancel')`, unchanged for the existing tests; `hs update`'s tests pass
  `('UPDATE', 'cancel', 'DELETE')`) and classifies `DELETE FROM task WHERE task.id = ?`. `hs update`
  runs in-process (`TaskUpdateApp.main(argv)`, `hypershell.task.ensuredb` replaced) on the per-test
  engine. A `meanwhile` change can only precede the transaction's first write (SQLite's write lock),
  so it targets the first row written. Legacy: lands on a re-run (exit 0); exhausted (exit 5, the
  contended error naming the task, row unchanged, no traceback line); missing id (exit 5, not found;
  guard). `--limit`: lands on a re-run with every change on every row; exhausted on the 120th of 150
  rows (exit 5, no row changed, no traceback); a selected row completed meanwhile is left with its
  result, not counted, the warning shown, exit 0 (update and `--cancel`); `--delete --limit` lands on a
  re-run and exhausted deletes nothing. Pins: an uncontended combined update without `--limit`
  (`group=7 color:red --remove-tag size -w group==0`) is one UPDATE of `task` for 5 and for 30 rows; an
  uncontended `--limit` update is one UPDATE per row with no lookup.
- **CLI, SQLite** (`tests/test_update.py`, subprocess): the three R5 lines of the GOAL; several tag
  keys, several `--remove-tag` names, a set with a removal (changes written before `-t`: `-t` takes
  every following word, research/02); "Updated N" equals the rows written.
- **Server database** (`tests/test_contended_write.py`, the only module CI's PostgreSQL jobs run among
  these): *fault injection in the server* — a `BEFORE UPDATE OR DELETE … FOR EACH ROW` trigger that
  skips a chosen row k times (research/01 §1) — for the filtered UPDATE (lands; exhausted: exit 5,
  nothing written) and the filtered DELETE (lands), red on every PostgreSQL job before the fix. *Real
  race* (`contend`, `hs update` as a subprocess through `tests.main`; `blocked_by` also recognises
  `DELETE FROM task`): legacy, filtered, `--limit`, `--delete` and `--delete --limit`, each with a holder
  on one row, outcome-only, red on CI's 2.31.0 jobs (upstream #10902 covers DELETE, research/03); and a
  holder that takes its row out of the filter (completes it) while `hs update -f -R group=5` waits: 3
  written, the warning, "Updated 3", red everywhere before the fix by its count. *R5 on PostgreSQL*:
  the GOAL's three lines and the multi-key cases through the CLI. *Pin*: an uncontended field update
  without `--limit`, in-process on the bound session, is one UPDATE of `task`, with as many statements
  on `task` for 5 rows as for 30 (after the fix, one UPDATE and the residual SELECT; a guard, green
  before and after).
- **Red lists**: [`red.txt`](red.txt) (red locally on the base `f00f853`, checked by
  `spec/relay-hardening/drive/red_check.sh`) and [`ci-red.txt`](ci-red.txt) (CI-only), judged on CI by
  `drive/ci_check.py`, copied from `spec/contended-writes/drive/` (it reads its own spec directory).

### 2.9 Docs, AGENTS.md, invariants (R7)

- `docs/cli/task_update.rst`: `.. versionchanged:: 2.9.0` entries for user-visible changes 1–6
  (combined changes; `--limit`'s write-time re-check and single transaction; counts and the warning;
  contended → nothing written, exit 5; contended rows written; legacy "not found").
- `docs/_include/task_update_desc.rst` (also in `docs/manual.rst`): a paragraph saying what the counts
  mean, that a filter is re-checked when each task is written, and that a contended update writes
  nothing and exits 5 so it can be rerun; the `-t` placement note. No option changes, so no help text,
  `share/` completion or `docs/_include/task_update_help.rst` change.
- AGENTS.md § Data layer specifics and invariants §1: drop "`hs update` does not follow it yet
  (`issues/hs-update-contended-writes.md`)"; add `hs update` to the pattern list (legacy by id, raising
  on a gone row; `--limit` by id under the user's filter; the set-based form with the PostgreSQL
  residual read; all-or-nothing at the bound via `Task.Contended`). AGENTS.md § High-risk files'
  `data/model.py` line may name `__write_rows`' filter condition. `.agents/factory/invariants.md` §1 in
  lockstep.

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1 | §2.3 `abort`; §2.4 `Task.update` on `__write_rows`; §2.7 `Task.Contended` → exit 5; `StaleDataError` → not found only for a missing row |
| R2 | §2.1 criteria; §2.3 condition `'filter'`; §2.5 per-row re-check; §2.6 set-based + PostgreSQL residual; DELETE on both |
| R3 | §2.5/§2.6 return the rows written; §2.7 WARNING when lower than the count, INFO line, exit 0 |
| R4 | §2.3 `abort` rolls back the one transaction; §2.5 one transaction for `--limit`; §2.7 mapped, no traceback |
| R5 | §2.2 one SET for every change, set-based and per-row; §2.1 one filter |
| R6 | §2.8; TECH P1 (tests first), P2 (CI red), P7 (CI green) |
| R7 | §2.9; TECH P6 |

## 3. Invariant gate (AGENTS.md constitution check)

Gate #1 (before research): §1 (the 0-row-match rule, which names `hs update` as its last exception;
the state predicates `--cancel`/`--revert` write), §2 (`CANCEL_STATUS` is what `--cancel` writes), §3
(the unguarded `retried` reset from `hs update`, a non-goal), §12 (docs, tests). Touches
`data/model.py` (high blast radius) and `task.py`. The GOAL is sane against them: it moves the last
exception onto the §1 pattern. Gate #2 (on this design):

- **§1 state predicates.** No new state query. `--cancel` still writes `schedule_time`,
  `completion_time` and `exit_status = CANCEL_STATUS` together; `--revert` clears exactly
  `Task.revert_columns` (now by reference, not a copy).
- **§1 0-row rule.** Every per-row write checks its own count; on 0 a fresh read (the lookup) says
  whether the row still qualifies (exists, for the legacy form; matches the user's filter, otherwise);
  re-run at most `CONTENDED_RERUNS` times with DEBUG per extra attempt; at the bound the WARNING naming
  the task and path, then a safe outcome: nothing written, the error, exit 5 (no crash, no stranded
  task, no write without its condition). No `bulk_update_mappings` or ORM flush remains for a `task`
  write. The set-based form is the one bend (see the table).
- **§1 transition logic in `Task` classmethods.** Every write moves from `task.py` into `Task`.
- **§3.** `hs update` can still reset `retried` (non-goal; unchanged and still listed).
- **§4.** Not a server path.
- **§12.** Tests tagged `unit`/`integration`; docs with `versionchanged:: 2.9.0` in the same commit;
  no option change, so no `share/` or help-snippet change; no version or dependency change (the JSON
  functions and `RETURNING` are in the current floors: SQLite JSON1, PostgreSQL 13, SQLAlchemy 2.0.50).

### Deviation justifications

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| The set-based form (no `--limit`) re-checks its rows with one residual read on PostgreSQL even when every row matched first time (§1: "a first-attempt match is neither re-run nor re-checked") | A set-based UPDATE has one count for all rows; which rows it missed is only knowable by reading after it. The read re-runs nothing that matched | Per-row writes without `--limit` grow the statement count with the rows, which the GOAL gate ruled out |
| The PostgreSQL set-based UPDATE holds its written ids in memory (`RETURNING`) | Rows it wrote that stay in the filter must be told from rows it missed | `xmin` fails on columnstore chunks (research/01 §2); a server-side `<> ALL(:ids)` is quadratic on PostgreSQL 13; a temp table needs the TEMP privilege |
| The set-based form's fault injection runs on PostgreSQL (a trigger), not SQLite (R6 names SQLite for R1–R4) | SQLite cannot miss a row in a set-based write, so its code takes no residual and SQLite has nothing to inject there; SQLite still carries R1–R4 through the legacy and `--limit` paths and the shared re-run | A SQLite residual (`RETURNING`, 3.35+) would cost memory on every SQLite update for a miss SQLite cannot have; **foreman decision D1** |
| `__write_rows` gains `where`, SET expressions, `delete` and `abort` | One re-run loop for every task write (§1 pattern in one place) | A second loop for `hs update` would duplicate it |

## 4. Rabbit holes (resolved)

- **Telling a missed row from a written one in a set-based UPDATE** → `RETURNING id` + residual read on
  PostgreSQL; nothing on SQLite ([research/01](research/01-residual-and-limit.md) §2). `xmin` works on
  plain tables and uncompressed chunks but raises "transparent decompression only supports tableoid
  system column" on columnstore chunks (probed, TimescaleDB 2.32).
- **The user's filter as a per-row condition** → self-contained criteria on `SearchableMixin`, usable in
  SELECT/UPDATE/DELETE and `id = :id AND …` ([research/02](research/02-filter-and-change.md) §Q4);
  matched today's rows for every filter tried on all three providers, except the SQLite `-t a b` bug.
- **Does a contended DELETE miss on 2.31?** → yes: upstream #10902 ("The same happens with a waiting
  DELETE (DELETE 0, row remains)"), affected 2.28.1–2.31.0, fixed by #10906 in 2.32.0
  ([research/03](research/03-contended-delete.md)). R6's `--delete` real race is expected red on CI.
- **The PostgreSQL multi-key variant** → reproduced on PG 18 and TimescaleDB 2.32 (second key missed,
  second `--remove-tag` name kept, "Updated 4 tasks"); also the set-then-remove variant on SQLite
  (research/02 §Q2). Fixed by one SET (§2.2).
- **One transaction for `--limit`** → same write cost as the batches; the lock is held to the end
  (research/01 §3).
- **Forcing the set-based miss without 2.31** → a row trigger returning NULL (research/01 §1).

## 5. Risks & open questions

- **D1 (foreman): the filtered form's fault injection is on PostgreSQL.** See the deviation table. The
  trigger tests are red on every PostgreSQL job at the tests-first commit (as the two-session source
  test was in #109, plan-gate ruling 1); "green throughout" reads as "the real-race tests never fail
  there".
- **D2 (foreman): the SQLite `-t a b` fix reaches `hs list`.** The non-goals exclude `hs list` changes,
  but per-row writes need a self-contained criterion, and the count and the write must share one
  filter. Recommended: fix it in the shared builder (a bug fix, `hs list` on SQLite then matches
  PostgreSQL), with a CHANGELOG-worthy note in the docs. Alternative: keep `build_query`'s text clause
  for `hs list` only and give `hs update` the corrected criteria (count and write consistent; `hs list`
  and `hs update` then disagree on SQLite for that input); or seed it and reproduce the last-tag-wins
  behaviour in the criteria (not recommended).
- **D3 (foreman): `--limit` on SQLite stores booleans as 0/1 and nests dotted keys**, as the filtered
  form does today (§2.2). Recommended: accept (one representation for both forms).
- **D4 (foreman): an update with nothing to change** logs `Updated 0 tasks` with no warning (§2.7).
  Literal R3 would warn that every counted task "no longer matched", which is false.
- **D5 (foreman): the contended error is logged at CRITICAL** through `handle_exception`, as
  `Task.NotFound` is, after the helper's WARNING. R4 says "log an error"; tests check level ≥ ERROR.
- **Risk: memory of the PostgreSQL set-based UPDATE** (§2.6), about 100 bytes per written id, plus a
  read-back of written rows still in the filter. If it matters for large hypertables, a temp table of
  written ids is the follow-up (needs the TEMP privilege).
- **Risk: SQLite write lock for a long `--limit`** (research/01 §3): another writer waits for the whole
  update; past pysqlite's 5 s busy timeout (about 350 000 rows locally) its write fails. Accepted.
- **Risk: CI 2.31 does not show a real-race test red** at P1 → P2 stops for the foreman; the fault
  injection's red stands.
- **Observation, out of scope:** AGENTS.md dates the hypertable miss "2.29+", upstream "2.28.1"
  (research/03 §1); a wording fix for a later docs pass. The GOAL Problem's multi-key command line is
  mis-parsed (`-t` takes every following word); the tests and docs place the changes before `-t`.

## 6. Verification strategy

| Test (module) | Base `f00f853`, locally (SQLite / PG 18 / TS 2.32) | Base, CI | After the fix |
|---|---|---|---|
| Fault injection, legacy lands / exhausted (`test_contended_recovery.py`) | red (assertion: exit 5 not found) | red, `tests (3.x)` | green |
| Fault injection, `--limit` lands / exhausted / changed meanwhile (update, cancel) (`test_contended_recovery.py`) | red (traceback exit 5; row overwritten) | red, `tests (3.x)` | green |
| Fault injection, `--delete --limit` lands / exhausted | red (row remains / partial) | red, `tests (3.x)` | green |
| Pin: combined update without `--limit` is one UPDATE (SQLite) | red (three UPDATEs) | red, `tests (3.x)` | green |
| Guards: legacy missing id not found; `--limit` one UPDATE per row, no lookup | green | green | green |
| CLI R5, SQLite (`test_update.py`): GOAL's three lines; set-and-remove | red | red, `tests (3.x)` | green |
| CLI R5, SQLite: several keys; several removals | green (one `json_set`/`json_remove`) | green | green |
| Trigger injection: filtered UPDATE lands / exhausted; filtered DELETE lands (`test_contended_write.py`) | red on PG 18 and TS 2.32 | red, every `PostgreSQL (…)` job | green |
| CLI R5 on PostgreSQL: three lines, several keys, several removals | red on PG 18 and TS 2.32 | red, every `PostgreSQL (…)` job | green |
| Real race, holder takes its row out of the filter | red (count) | red, every `PostgreSQL (…)` job | green |
| Real race, legacy / filtered / `--limit` / `--delete` / `--delete --limit` | **green** | red on `timescale`, `timescale-oss` (2.31.0) | green |
| Pin: uncontended field update, one UPDATE and as many statements for 5 rows as for 30 (PostgreSQL, in-process) | green | green | green |

- **P1 (tests only):** `src/` unchanged from `f00f853`; every `red.txt` line red on the base for the
  defect (`red_check.sh`, SQLite and the local PG 18 and TS 2.32); the rest of the suite green
  (`drive/rest_green.sh`); `tests/test_contended_write.py` otherwise green on both local servers, the
  real-race additions included.
- **P2 (CI red):** the draft PR's run at the P1 commit, `drive/ci_check.py red`.
- **P3–P5:** each slice's tests green; `red_check.sh` still red on the base; laptop drives
  (`drive/update_drive.sh`, SQLite through `.agents/factory/bin/temp_site.sh`, and the local
  PostgreSQL): the three reproductions (every row tagged; "Updated N" equal to the rows written), the
  multi-key lines, and a `--limit 4 -R` update whose prompt waits while another `hs update ID
  exit_status 0` completes one task (3 written, the warning, exit 0).
- **P6:** greps on AGENTS.md/invariants; `sphinx-build -W`; the full suite; the server-database modules
  on both local servers.
- **P7 (CI green):** the draft PR's run at the last code commit, every job green.

---

*Backing research: [`research/00-digest.md`](research/00-digest.md).*

## Plan-gate rulings (2026-11-15)

Foreman, gate judge under the maintainer's mandate. Approved as planned, with these rulings.

- **D1:** the filtered form's fault injection runs on PostgreSQL through the trigger, red on every
  PostgreSQL job at P1, as #109's ruling 1 set out. SQLite covers R1–R4 through the legacy and
  `--limit` paths.
- **D2:** fix the shared criteria builder, which also fixes `hs list -t a b` (two name-only tags)
  checking only the last tag on SQLite. A test covers `hs list` too, and the user-facing note lists
  it as a fix. This reaches `hs list` only because the builder is shared; no other `hs list`
  behaviour changes.
- **D3:** accepted. `--limit` on SQLite stores booleans as 0/1 and nests dotted keys, as the
  filtered form already does.
- **D4:** accepted. An update with nothing to change logs "Updated 0 tasks" and no warning.
- **D5:** accepted. The contended error is logged at CRITICAL through `handle_exception`, like
  NotFound, and tests check ERROR or above.
- **Risks:** both are accepted and get a sentence in the `hs update` docs. A long `--limit` holds
  SQLite's write lock, and another writer fails after its busy timeout. The PostgreSQL set-based
  update holds the written ids in memory.
- **Wording:** P6 corrects `AGENTS.md`'s dating of the TimescaleDB miss to the versions affected
  upstream (2.28.1 through 2.31.0, fixed in 2.32.0), as it rewrites that passage anyway.

## Review-cycle-1 rulings (2026-11-15)

The review's human gate was triggered by F1, a CONFIRMED breach of the §1 retry bound in
`data/model.py`. The maintainer signed off in session (2026-11-15) to fix all three findings.

- **F1.** On PostgreSQL without `--limit`, the set-based UPDATE or DELETE counts as each row's first
  attempt. A row it misses gets at most `CONTENDED_RERUNS` further per-row tries, so the total
  stays within §1's bound. The WARNING and the contended error report the true number of attempts.
  - **Tests:** a trigger skipping one row exactly `CONTENDED_RERUNS` times lands. One that skips it
    `CONTENDED_RERUNS + 1` times aborts with nothing written and exit 5. The attempt count in the
    message is checked. The tests are red on `ee5fcd4` where they differ.
  - **Unchanged:** the `--limit` and single-task forms, which were already within the bound.
- **F2.** `invariants.md` §1 describes `delete_matching` as it is built: the DELETE's row count plus
  one read of the ids still matching. It does not use `RETURNING`. `AGENTS.md` already has it
  right.
- **F3.** Add a test of a `--delete` that leaves behind a row which stopped matching: "Deleted N",
  the warning, and exit 0. Cover it with and without `--limit`.
