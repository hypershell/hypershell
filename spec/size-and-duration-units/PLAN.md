# PLAN — Sizes and durations written with common units are read as written

> **Status:** Draft for review · **Last updated:** 2026-11-11
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). Backing detail is in
> [`research/`](research/). Every design element traces to a GOAL R-ID.

## 1. Summary

Make `parse_bytes` a full-match size parser and rewrite the unused `parse_time` as a full-match
duration parser, both in `src/hypershell/core/types.py`, raising one error class that argparse and the
existing `ValueError` catchers both understand. The six duration options swap `type=int` for
`parse_time`; the memory options already use `parse_bytes`. The inline `timeout:` tag is converted in
`Task.new` beside `memory:`, and the tag validator checks `memory`/`timeout` values with their parsers
instead of the tag charset. Config-file strings and the log rotation policy are fixed by the parsers
alone. Every edit is placed so the squash commit cherry-picks onto `2.8.6` for 2.8.7 with one expected
conflict (`docs/whats_new.rst`, absent on 2.8).

## 2. Design

### Parsers — `src/hypershell/core/types.py` (identical on `2.8.6`)

- **`UnitError(ValueError, argparse.ArgumentTypeError)`**, exported. argparse prints an
  `ArgumentTypeError`'s own message (a `ValueError`'s is replaced by "invalid … value"), and
  `data/model.py`, `core/logging.py` and `submit.py` keep catching `ValueError`.
- **`parse_bytes(value: str) -> int`**: strip, then full-match (case-insensitive)
  `NUMBER \s* UNIT?` where `NUMBER = \d+(\.\d+)?` and `UNIT` is `K|M|G|T` optionally followed by `B`
  or `iB`, or a bare `B`. Scale 1024ⁿ. Result `int(float(num) * scale)`, the same arithmetic as today,
  so every R6 value is bit-identical. Anything else raises `UnitError` naming the value and the forms.
  `MEMORY_SCALES` is kept (keys stay valid); `MEMORY_PATTERN` is replaced.
- **`parse_time(value: str) -> int`** (kept name, already exported, no callers in `src/`): strip, then
  full-match (case-insensitive) one of
  1. a bare number `\d+(\.\d+)?` — seconds;
  2. unit components `[N d][N h][N m][N s]`, at least one, each unit at most once, in that order,
     no inner spaces (`45s`, `90m`, `1h30m`, `1d12h`, `1.5h`);
  3. Slurm `MM:SS`, `HH:MM:SS`, `D-HH`, `D-HH:MM`, `D-HH:MM:SS`, integer fields, no range check (Slurm
     normalises `90:00` itself).
  Result `math.ceil(total_seconds)`. `W` and `TIME_PATTERN`'s prefix match go; refusals (`5MB`, `1hr`,
  `2hours`, `1w`, `30m1h`) raise `UnitError` naming the value and the forms.

### Call sites

- `submit.py` `-W/--timeout`, `server.py` `-W/--task-timeout`, `client.py` `-T/--timeout` and
  `-W/--task-timeout`, `cluster/__init__.py` `-W/--task-timeout` and `-T/--timeout`: `type=int` →
  `type=parse_time`, and add `parse_time` to each module's existing `core.types` import line. Memory
  options keep `type=parse_bytes`. The `-g` line and metavars are not touched (develop added `--zone`
  next to `-g` in submit's help).
- `client.py` `check_args` `<= 0` checks stay (a `0s` timeout is refused there as today).
- `cluster/launch.py` is not touched: `client_options` forwards `str(int)`, which `parse_time` and
  `parse_bytes` read back as the same int (R6).

### Inline tags — `src/hypershell/data/model.py`

- **`ensure_valid_tag`**: inside the `if isinstance(value, str):` block, after the naked-tag
  `return`, a `memory` or `timeout` value is checked by `parse_bytes`/`parse_time` (a `UnitError` is a
  `ValueError`, so `split_argline` wraps it as today's `RuntimeError('Failed to parse inline tags
  (…)')`, and `hs submit -t`/`hs run -t` turn it into an argument error) and then skips the charset
  check. Only these two keys are popped by `Task.new` and never stored as tags; the change lets
  `timeout:1:30:00` through and gives `memory:8,000MB` a message that lists the forms (R5).
- **`Task.new`**: the memory line converts a string through `parse_bytes`; two lines inserted *above*
  the untouched `other['timeout'] = tag.pop('timeout', other.get('timeout', None))` normalise a string
  timeout (from `tag` or `other`) through `parse_time` into `tag['timeout']`. A `UnitError` here
  (reached by `--from-json` keys, which skip strict validation, or by a tag after a naked tag, which
  `ensure_valid_tag` stops checking) is re-raised as a `RuntimeError` naming the value and the forms,
  matching the inline-tag precedent (one log line, exit 5, no traceback file). Ints and floats pass
  through unchanged, as `memory` does today.

### Rotation policy — `src/hypershell/core/logging.py`: no edit

`initialize_logging` tries `parse_bytes(file_policy)` and falls to the time-based branch on
`ValueError`. With a full match, `30 2 * * *` is refused and becomes time-based; `2GB` and now `2G`
stay size-based (R7). `SizeRotatingFileHandler.reset_interval` uses the same parser.

### Docs and help (R8, R9)

- `APP_HELP` strings for the options above (`submit.py`, `server.py`, `client.py`,
  `cluster/__init__.py`) and their hand-maintained snippets `docs/_include/{submit,server,client,
  cluster}_help.rst`: memory options state that `K`, `M`, `G`, `T`, with or without `B` or `iB`, are
  powers of 1024 (`16G` = `16GB` = `16GiB`); duration options state seconds, `1h30m`/`1.5h`/`1d12h`, or
  `[D-]HH:MM:SS`/`MM:SS`/`D-HH[:MM]`. The inline-tag examples in those paragraphs
  (`#HYPERSHELL: memory:8GB`, `timeout:3600`) are where the inline tags are documented; they say the
  tag takes the same forms.
- `docs/_include/config_param_ref.rst`: `task.memory`, `task.timeout`, `client.memory`,
  `client.timeout`, and `logging.file.rotate` (sizes take the same units; a cron expression may start
  with a digit). `docs/logging.rst` rotate prose likewise.
- `share/zsh/site-functions/_hs` descriptions "Memory in bytes" / "… in seconds" for these options.
  Bash completions and man pages are not touched (man pages are rebuilt at release).
- `docs/whats_new.rst`: one *Not yet released → Fixes* entry carrying R9 and ending "This also ships in
  2.8.7." No `versionchanged` marker (decision 5 below).

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1   | `parse_bytes` full-match grammar, K/M/G/T ± B/iB, any case, 1024ⁿ; unchanged `type=parse_bytes` on the five memory options; `Task.new` memory line; `ensure_valid_tag` memory check |
| R2   | `parse_time` unit grammar with fractions, `ceil`; `type=parse_time` on the six duration options |
| R3   | `parse_time` Slurm grammar `MM:SS`, `HH:MM:SS`, `D-HH[:MM[:SS]]`; bare integer stays seconds |
| R4   | `ensure_valid_tag` admits `timeout` values `parse_time` accepts; `Task.new` converts a string timeout before the untouched pop line |
| R5   | Full-match grammars; `UnitError` message names value and forms; argparse shows it (exit 2); inline/JSON refusals are a one-line `RuntimeError` (exit 5) |
| R6   | Same float-then-`int` arithmetic for sizes; bare integer → bytes/seconds; ints/floats in `Task.new` untouched; `str(int)` forwarding round-trips |
| R7   | Full-match `parse_bytes` refuses digit-leading cron; existing `except ValueError` branch in `initialize_logging` |
| R8   | `APP_HELP` + `_include/*_help.rst` + `config_param_ref.rst` + `logging.rst` + zsh descriptions |
| R9   | `docs/whats_new.rst` entry (develop); same sentence in the squash commit body for 2.8.7's release notes |
| R10  | New `tests/test_size_duration.py`: parser accept/refuse tables, each option, both inline tags, a config/env string default, rotation policy |

### Decisions on GOAL's plan-stage questions

1. **Config-file values reach the same parsers, unchanged.** A string in `[task] memory/timeout` or
   `[client] memory/timeout` (or `HYPERSHELL_TASK_TIMEOUT=1h30m`) is an argparse string default, and
   argparse runs string defaults through `type=` (cmdkit's `Interface` does not change parsing). So
   swapping `type=int` covers config with no config-model edit, which the GOAL's constraint forbids
   anyway. TOML/env numbers skip `type=` and keep today's meaning. A bad config string fails as
   `argument -W/--timeout: Duration '1hr' …`, naming the value. One test pins the env path. Reason:
   zero extra code and zero cherry-pick surface ([01](research/01-config-and-call-paths.md)).
2. **Rounding: sizes floor, durations ceil.** Sizes keep `int(float(num) * scale)` (floor), the exact
   arithmetic today, so R6 holds bit for bit (`1.3K` → 1331). Durations round **up** to whole seconds
   (`0.5s` → 1): a timeout of `0` means "no timeout" across the code (`X or None`, `if not timeout`),
   so flooring a positive fraction would silently remove the limit. Both return `int`, which
   `client_options` needs.
3. **Upper-case duration units are accepted** (`1H30M`, `90S`). Matching is case-insensitive, like
   the memory units (R1, "any case") and like today's `parse_time` (which upper-cases). No ambiguity
   arises: there is no month unit, and `5MB` is still refused because `B` is not a duration unit.
4. **A bad inline tag keeps today's behaviour: earlier tasks stay.** `hs submit` already raises out of
   its loader on an invalid line after earlier bundles are committed (default bundlesize 1), exits 5,
   and logs one `RuntimeError` line; `issues/ingest-robustness.md` defers all-or-nothing ingest. This
   fix makes a bad `memory:`/`timeout:` follow that precedent (one line naming the value and forms,
   exit 5) rather than today's `ValueError` traceback file. On develop, a named file in database mode
   is counted up front through `split_argline`, so a value the validator refuses there is refused
   before anything is written; on 2.8.6, stdin, `--queue` and `hs server` the submit is partial, as
   for any invalid line. The test pins the exit status and message, not the partial count (that would
   pin a deferred flaw) ([02](research/02-inline-tags-and-submit.md)).
5. **No `versionchanged` marker; one `whats_new` entry.** AGENTS.md § Docs marks behaviour the stable
   line lacks or does differently. Once 2.8.7 ships the stable line has this fix, so a `2.9.0` marker
   would be false and a `2.8.7` marker would be a first-of-kind convention. The #92 precedent (in 2.8.6
   and develop) used no marker and a *Not yet released → Fixes* entry ending "This also ships in
   2.8.6."; this fix does the same with 2.8.7. Master has no `docs/whats_new.rst`, so the R9 sentence
   also goes in the squash commit body, which `hs-release` drafts the 2.8.7 GitHub release notes from
   ([03](research/03-docs-and-release-markers.md)).

None of these changes a GOAL R-ID; nothing is escalated.

### 2.8.6 vs develop drift at the touched sites

- `core/types.py`: identical. `core/logging.py`: not edited (call sites at 2.8.6:457/783 vs 463/1055).
- Option definitions: the same text in every module, at different lines — submit 921-926 vs
  1281-1286, server 1391-1395 vs 1596-1600, client 1541-1567 vs 1611-1637, cluster 206-224 vs
  225-243. `APP_HELP` blocks match in server, client, cluster; **submit's differs** (develop has
  `--zone` one line after `-g`), so the edit touches only the `-m`/`-W` lines.
- **`data/model.py` `Task.new` differs** (develop adds `strict_tag`, `parse_inline`, fingerprint and
  source). The memory/timeout lines match, but the line after the timeout pop is develop-only — hence
  the insert-above design. `ensure_valid_tag` differs above the value block (develop's `strict`
  parameter); the value block itself matches.
- `docs/whats_new.rst` does not exist on 2.8.6 (modify/delete conflict; resolve by dropping it). All
  help/config/logging doc paragraphs touched are byte-identical on 2.8.6.
- `tests/test_submit.py`, `tests/test_logging.py` differ heavily; new tests go in a **new file**
  `tests/test_size_duration.py`, using only helpers both lines have (`tests.main`, `temp_site`).
- develop only: `[relay] timeout` (forwarded as `-T` text; out of scope); `split_argline` up-front
  counting in `prepare_source`.

## 3. Invariant gate (AGENTS.md constitution check)

Walked before research and again against this design.

- §1 Task lifecycle — `Task.new` only converts two column values before construction; no query,
  predicate or state transition changes. The change stays in the `Task` classmethod.
- §3 Retry model — retries call `Task.new` without memory/timeout (pre-existing, unchanged); stored
  ints are never re-parsed.
- §5/§6 Concurrency, shutdown — `client.py`/`server.py` change only an option's `type=`; no FSM or
  thread code. A loader error still stops the submit thread through the existing path.
- §10 Configuration — no config-model change; string defaults go through `type=` as argparse already
  does; `0` keeps meaning unlimited (durations round up so a fraction never becomes `0`).
- §11 Cluster orchestration — `client_launch()`/`client_options` untouched; the launched argv carries
  `str(int)` as before, so a 2.9.0a4 client still accepts it (contract test unchanged). Keys untouched.
- §12 Conventions — help snippets and zsh completions change in the same commit as the CLI change;
  `whats_new` entry; tests marked `unit`/`integration` with `@mark.parametrize`; exit codes come from
  the existing exception mapping (`RuntimeError` → `runtime_error`, argparse → `bad_argument`); no
  version literal.

### Deviation justifications

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| `ensure_valid_tag` checks `memory`/`timeout` values with their parsers instead of the tag charset | The charset refuses `:`, so R4's Slurm forms never reach `Task.new`; and `8,000MB` would be refused without naming the forms (R5) | Allowing `:` in every tag value widens what any stored tag may hold; exempting the keys without parsing lets `hs update` store arbitrary text in a `memory`/`timeout` tag |
| `Task.new` normalises `tag['timeout']` in lines inserted *above* an untouched `other['timeout'] = tag.pop(…)` | Rewriting that line conflicts on 2.8.6 (the next line is develop-only); the insert-above form merges cleanly (`git merge-file` simulation) | Rewriting the line reads more naturally but costs a hand-resolved conflict in the highest-blast-radius file on the release-day cherry-pick |
| One error class with two bases (`ValueError`, `ArgumentTypeError`) | argparse shows only an `ArgumentTypeError`'s message (R5), while model/logging/submit catch `ValueError` | A separate CLI wrapper per parser doubles the call-site edits in four modules |

## 4. Rabbit holes (resolved)

- Do config-file strings bypass the parsers? → No; argparse converts string defaults through `type=`
  ([01](research/01-config-and-call-paths.md)).
- Does the inline `timeout:1:30:00` even reach `Task.new`? → No; the tag charset refuses the colon.
  Resolved by the validator change ([02](research/02-inline-tags-and-submit.md)).
- Will the `Task.new` edit cherry-pick? → Only if the timeout pop line is left byte-identical
  ([02](research/02-inline-tags-and-submit.md)).
- Marker for a fix in two releases? → None, per #92; the note rides in `whats_new` and the squash body
  ([03](research/03-docs-and-release-markers.md)).
- Does the rotation policy need code? → No; the parser change alone fixes R7.

## 5. Risks & open questions

- **Cherry-pick risk: low.** Expected conflicts: `docs/whats_new.rst` (modify/delete — `git rm` it) and
  `spec/` (drop it from the 2.8 pick). Everything else is simulated clean by the P2 verify
  (`git merge-tree --merge-base=<plan commit> 2.8.6 HEAD`). The new test file must also be run on the
  2.8 branch after the pick (2.8's `Task.new` has no `strict_tag`/`parse_inline`; tests must not use
  them). That run belongs to the release step.
- An error from a bad *config* string is reported as `argument -W/--timeout: …` though the user typed
  no `-W`; it names the value, which is enough.
- Partial submit on a bad inline value remains (deferred to `issues/ingest-robustness.md`).
- Follow-up seeds, out of scope: `hs update timeout=1h` stores text in an INTEGER column; a retry
  drops the parent's memory/timeout/cores; `[relay] timeout` forwarded as text; a TOML float timeout
  forwarded as `5400.0`; duration units on the remaining options
  (`issues/duration-units-remaining-options.md`, per GOAL).

## 6. Verification strategy

- **Unit** (`tests/test_size_duration.py`, `@mark.unit`, parametrized): every R1/R2/R3/R6 form → exact
  int; every R5 refusal raises `UnitError` whose message names the value and the forms; ceil and floor
  cases; upper-case durations; `parse_bytes('30 2 * * *')` refused; each app's option parses `16G` /
  `1h30m` / `1:30:00` and refuses `16X` / `1hr` with the forms in the message; `ensure_valid_tag`
  accepts `timeout:1:30:00` and refuses `memory:8,000MB` with the forms.
- **Integration** (`@mark.integration`, `temp_site`): `hs submit` a file with inline `memory:16G` and
  `timeout:1:30:00`/`1h30m` and options `-m 1.5GiB -W 1.5h`; read the stored values back
  (17 179 869 184, 5 400, …); a bad inline `timeout:1hr` exits 5 naming the value;
  `HYPERSHELL_TASK_TIMEOUT=1h30m` reaches a submitted task as 5 400; a CLI run under
  `HYPERSHELL_LOGGING_FILE_ROTATE='30 2 * * *'` configures time-based rotation and `2G` size-based.
- **CLI drive** in a throwaway site (`.agents/factory/bin/temp_site.sh`), the Sphinx `-W` build, and
  the cherry-pick dry run, as each phase's `verify:` in [`TECH.md`](TECH.md).

---

*Backing research: [`research/00-digest.md`](research/00-digest.md).*
