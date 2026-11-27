# 02 — Inline tags, submit error handling, and the Task.new edit site

Read-only research for `size-and-duration-units`. "develop" here means `HEAD` / `origin/develop`
(`50e64a9`); the local `develop` ref is stale and differs from it. "2.8" means tag `2.8.6`.

## 1. How inline tags become values

`Task.split_argline` (`data/model.py:569-584`; 2.8.6 `:358-373`, identical text) splits the comment
on whitespace, then `Tag.from_cmdline` (`core/tag.py:33-41`, same on 2.8.6) splits each token on the
**first** colon only and runs `smart_coerce` (`core/types.py:33-47`) on the value. It then calls
`ensure_valid_tag(tags)` in strict mode, whose value check is `^[A-Za-z0-9_.+-]+$`
(`model.py:618`; 2.8.6 `:399`). **A colon or comma in a value is refused there**, wrapped as
`RuntimeError('Failed to parse inline tags (...)')` (`model.py:576-577`). Checked with a copy of the code:

| Inline | Value after parse | Passes `ensure_valid_tag`? |
|---|---|---|
| `timeout:1:30:00` | `'1:30:00'` (colon survives the split) | **No**, colon |
| `timeout:1-12:00:00` | `'1-12:00:00'` | **No** |
| `timeout:90:00` | `'90:00'` | **No** |
| `timeout:1h30m` | `'1h30m'` | yes |
| `timeout:1.5h` | `'1.5h'` | yes |
| `timeout:90` | `90` (int) | yes, not a str |
| `timeout:1.5` | `1.5` (float) | yes |
| `memory:16G` / `memory:1.5GiB` | `'16G'` / `'1.5GiB'` | yes |
| `memory:8,000MB` | `'8,000MB'` (comma is not a separator) | **No**, comma |

**R4 needs a validator change.** The Slurm forms never reach `Task.new`. Changing only `Task.new`
leaves `timeout:1:30:00` refused with "Tag value must only contain alphanumerics…". The same check
guards `hs submit -t` (`submit.py:1406-1412`, ArgumentError, exit 2). Options: let `:` through for
the `timeout` key only, since its value is popped and never stored as a tag, or allow `:` in every
value, a wider change. A narrow edit to the value-regex line (`model.py:618`) merged cleanly onto
2.8.6 in a `git merge-file` simulation.

Edge cases for the plan:
- `smart_coerce` also yields `1e3`→`1000.0`, `1_000`→`1000`, `inf`/`nan`→float, `true`→`True`. None
  of these is a str, so an `isinstance(str)` gate passes all of them through. `inf` would break
  `timedelta(seconds=…)` at `client.py:559` (pre-existing).
- Inline values cannot hold a space. `memory:16 GB` becomes `memory:16` plus a naked tag `GB`, and
  `memory:8GB of ram` becomes `8GB` plus naked tags `of` and `ram`, with no error.
- An existing quirk: `ensure_valid_tag` does `return`, not `continue`, on an empty (naked) value
  (`model.py:615`; 2.8.6 `:396`). After a naked tag, later tags are not checked, so
  `# HYPERSHELL: x timeout:1:30:00` passes today.
- `8,000MB` as an inline tag is refused by the charset check, whose message names the value but
  not the accepted forms (R5).

## 2. What happens when `Task.new` raises

Every multi-line path runs `Loader` (`submit.py:353-375`; 2.8.6 `get_task` `:151-172`) in a
`LoaderThread`. The error is stored (`core/thread.py:32-37`) and re-raised from `SubmitThread` or
`LiveSubmitThread` `loader.join()` (`submit.py:639`, `880`). `submit_from` catches it, calls
`thread.stop()` and re-raises (`submit.py:1008-1012`; 2.8.6 `:761-765`). `stop()` halts the
committer, and `fsm.next()` returns HALT before FINAL (`core/fsm.py:47-53`), so **buffered tasks are
dropped, but bundles already committed or sent stay**. The default `submit.bundlesize` is 1
(`core/config.py:126`), so in practice every earlier line has been committed: a partial submit.

- **Exit and message.** A `RuntimeError` (the inline-tag precedent) logs
  `CRITICAL RuntimeError: Failed to parse inline tags (…, from: "<line>")` and exits 5
  (`core/exceptions.py:137`; cmdkit `runtime_error=5`), with no traceback and no line number. A bare
  `ValueError` (from `parse_bytes` today) also logs `Uncaught exception from <Loader>` with a
  traceback (`fsm.py:58-63`), then falls to the `Exception` handler, which writes a traceback file
  (`exceptions.py:142`). That also exits 5, but it reads as a crash.
- **`hs submit` in database mode with a named, seekable file (develop only).** `prepare_source` counts
  the file up front through `line_is_task`, which calls `split_argline` (`submit.py:137`, `1485`). An
  inline tag that fails `ensure_valid_tag` is therefore refused **before the Source row or any task is
  written**. A value that passes the charset check but fails in `Task.new` (`timeout:1hr`,
  `memory:16X`) is caught only in the Loader, so the submit is partial. 2.8.6 has no upfront count
  (no `line_is_task` or `source_fingerprint_and_count` in `src/`), so every bad line gives a partial
  submit there.
- **stdin, pipes, and `--queue`.** These stream, with no upfront count, so the submit is partial on
  both versions. In queue mode, bundles already sent may already be running.
- **Single task** (`hs submit ARGS`, `submit.py:1391`; `hs run`, `task.py:445`). `Task.new` runs
  before `Task.add`, so nothing is written. The error exits 5.
- **`hs server FILE`.** There is no gate (`server.py:1678`, raw `input_stream`). `SubmitThread`
  commits earlier lines, but scheduling waits for the whole file (`server.py:1115-1120`; 2.8.6
  `:930`), so in database mode nothing runs. The rows stay in the database for a later `--restart`.
  With `--no-db` (`LiveSubmitThread`), earlier lines are already queued, and may be running, when
  the bad line raises. `serve_from` stops the threads and re-raises (`server.py:1326-1330`); exit 5.
- **`hs cluster` / `hsx`.** `hsx` is `main(['cluster', …])` (`__init__.py:147-149`). On develop,
  `ClusterApp.source` gates a named file in database mode (`cluster/__init__.py:473-519`), so the
  behaviour matches `hs submit`. Otherwise it follows `hs server`.
- **Relay.** Relay code never calls `Task.new`, so it does not apply.
- **Precedent and tests.** No test pins the bad-line behaviour. `tests/test_data/test_model.py:21-44`
  covers only valid `split_argline` cases, and `tests/test_submit.py:222-238` covers only valid
  `memory:`. A bad `group:` is not validated at all (`issues/group-tag-integer-validation.md`). The
  partial-submit behaviour is recorded as a known, deferred flaw in `issues/ingest-robustness.md`
  ("raises out of the loader … after part of the file may already be submitted").

## 3. Other routes for `timeout=` into `Task.new`

- From kwargs: the `Loader` passes `timeout=self.timeout` (`submit.py:358`, `389`), and so do
  `submit_one` (`:1391`), the server and cluster submitters (`server.py:1066-1072`), and the CLI
  default `config.task.timeout or None` (`submit.py:1285`). This is an int today, but it is a str if
  a config file says `"1h"`, and that skips argparse `type=`. Gate on the **resolved** value
  (`tag.pop('timeout', other.get('timeout'))`), as the memory line already does. That covers this
  case for free.
- From tags: `-t timeout:…`, a global tag-only line (the Loader stores the raw string in
  `self.tags`, `submit.py:368-372`, and `Task.new` re-parses it on every later line; a bad global
  line raises on its own `Task.new` call), and `--from-json` record keys (`strict_tag=False`,
  `parse_inline=False`, `submit.py:388-390`). JSON can carry `"1:30:00"`, `"90"`, `90` or `1.5`. A
  bare-integer string must still mean seconds (R6), as `tag={'memory': '2000'}` is pinned at
  `test_submit.py:236`.
- **Retries** (`model.py:807-815`; 2.8.6 `:571-576`) call `Task.new` with `tag=task.tag` and the
  stripped `args`, and pass no cores, memory or timeout. A stored int is never re-parsed. **A retry
  also drops the parent's timeout, memory and cores**, on both versions. That is pre-existing and
  out of scope, but worth a seed.
- `hs update` never calls `Task.new` (see 4).

## 4. The `Task.columns` type map

`'timeout': int` (`model.py:394`; 2.8.6 `:292`) is only ever tested as `is str` or `is datetime`:
in `hs update` (`task.py:1080`, `1232`), in `-w` filters (`task.py:1346`) and in the relay store
(`relay/store.py:188`). Non-str fields go through `smart_coerce`, so `hs update timeout=1h30m` stores
the text. That is out of scope (not named in R1-R4), and the map is not relevant to this fix.

## 5. Edit site and cherry-pick safety

develop `model.py:524-531`:
```
        other['cores'] = tag.pop('cores', other.get('cores', None))
        # A memory value may arrive … (3 comment lines, 525-527)
        memory = tag.pop('memory', other.get('memory', None))
        other['memory'] = parse_bytes(memory) if isinstance(memory, str) else memory
        other['timeout'] = tag.pop('timeout', other.get('timeout', None))
        if fingerprint is None:
```
2.8.6 `:346-353` has the same lines, except that the line after the timeout line is
`return Task(id=uuid(), args=args,`. The lines before `other['group']` also differ on both sides.

I simulated each candidate with `git merge-file` (base = develop, theirs = develop plus the edit,
ours = 2.8.6):
- Rewriting line 530, deleting it, or inserting after it: **conflict** in every case.
- Inserting before line 530 and leaving it byte-identical: **clean**. Changing the memory line 529
  or the import line (`:35`, adding `parse_duration`) at the same time is also clean.

**Recommendation.** Normalise the value in `tag` just above the untouched pop line:
```
        timeout = tag.get('timeout', other.get('timeout', None))
        tag['timeout'] = parse_duration(timeout) if isinstance(timeout, str) else timeout
        other['timeout'] = tag.pop('timeout', other.get('timeout', None))   # unchanged
```
If a reviewer finds this awkward, the alternative is to rewrite line 530 and accept a one-hunk,
mechanical conflict on 2.8. Wrap the parser's `ValueError` in a `RuntimeError` inside the inserted
lines (and on line 529 for memory) so the refusal reads like the inline-tag precedent, not a
traceback.

## Recommendation: are tasks read before a refused tag kept?

Follow the existing behaviour and do not add a rollback. The precedent is to raise out of the
loader: earlier committed lines are kept, the command exits 5 with a `RuntimeError` message, and on
develop a named file in database mode is refused up front when the bad value fails during the
count. All-or-nothing ingest is the deferred `issues/ingest-robustness.md`, and adding it here would
widen the 2.8 cherry-pick. Two cheap changes keep the fix consistent with the precedent:
1. Raise `RuntimeError` (exit 5, one line naming the value and the accepted forms), not a bare
   `ValueError`.
2. State the partial-submit behaviour in TECH.md, and pin it with one test: a stream whose second
   line carries a bad `timeout:` exits 5 and keeps line 1.

Validating sizes and durations inside `split_argline` would also give develop's named-file path a
refusal before anything is written. But it would not cover `-t`, JSON or stdin, and 2.8 has no
upfront count, so leave that to the ingest seed.
