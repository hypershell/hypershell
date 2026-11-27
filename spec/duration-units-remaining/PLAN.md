# PLAN — Every duration option takes the forms the timeouts take

> **Status:** Draft for review · **Last updated:** 2026-11-14
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). Backing detail is in
> [`research/`](research/00-digest.md). Every design element traces to a GOAL R-ID.

## 1. Summary

Reuse a7's duration parser everywhere. The eleven whole-second declarations (`-w`, `-S`, `-P`, `-Q`,
`-n`) swap `type=int` for `parse_time`, which also covers their four configuration keys and
environment variables, because argparse runs a string default through `type=`. `--delay-start` gets
`parse_delay`, a sibling of `parse_time` that shares its matcher, takes one leading `-` for the random
form and keeps fractions. `hs relay`, the one command that reads `autoscale.period` without an option,
converts it in `RelayApp` before it starts. The launched client command line stays bare numbers, with
the delay written so that no exponent reaches a client. Three phases, each carrying its own help,
docs, completions and tests.

## 2. Design

### Parsers — `src/hypershell/core/types.py`

- **Shared matcher.** The pattern loop of `parse_time` moves into a private helper that returns the
  exact `Fraction` of seconds for a stripped text, or `None`. `parse_time` returns `math.ceil` of it
  or raises today's `UnitError` (same patterns, same message, same results). The a7 tests in
  `tests/test_size_duration.py` pin that.
- **`parse_delay(value: str) -> float`**, exported. Strip; take one optional leading `-`; match the
  rest with the helper; return `float(±seconds)`, unrounded. `-0` is `-0.0`, which `wait_start`
  treats as `0`, as `float('-0')` is today. Refusal raises `UnitError` whose message is exactly
  `parse_time`'s message for the same text, followed by one clause naming the random form written
  attached (`; a leading - waits a random time up to that duration (e.g., --delay-start=-10m)`). A
  `Fraction` too large for a float is refused with the same message. No space after the sign
  (`- 10m`), no `+`, no second sign.

### Option declarations (R1, R2)

| Declaration | Change |
|---|---|
| `submit.py` `-w` | `type=int` → `type=parse_time` |
| `server.py` `-w`, `-P`, `-Q` | `type=parse_time` |
| `client.py` `-w`, `-S` | `type=parse_time` |
| `client.py` `-d` | `type=float` → `type=parse_delay` (import added) |
| `cluster/__init__.py` `-w`, `-S`, `-P`, `-Q` | `type=parse_time` |
| `cluster/__init__.py` `-d` | `type=parse_delay` (import added) |
| `task.py` `-n` (`hs wait`, `hs run`) | `type=parse_time` (import added) |

Defaults, dests and metavars are unchanged. `DEFAULT_DELAY` (int `0`) and `DEFAULT_INTERVAL` (int
`5`) are not strings, so argparse never runs them through the new types. No check in `check_args`
changes. `ClientThread.wait_start` (`client.py:1332-1342`) is untouched.

### Configuration and environment (R3, R5)

No configuration-model edit. A string in `submit.bundlewait`, `task.signalwait`, `autoscale.period`
or `server.poll` (TOML string or a non-numeric environment value) is an argparse string default and
reaches `parse_time`. A number skips `type=` and keeps today's meaning (cmdkit coerces `60` → `int`,
`0.5` → `float`). Probed in [02](research/02-config-and-relay-period.md): no string reaches the
autoscalers of `hs cluster` or `hs server` by any path other than the option. A bad string fails as
`argument -P/--period: Duration '5MB' is not valid: …`, a7's established form.

### `hs relay` — `src/hypershell/relay/__init__.py`, `relay/site.py` (R3, R4)

- `RelayApp.site_period` (cached property): `config.autoscale.period`; a `str` goes through
  `parse_time`, anything else passes unchanged (the rule argparse applies to a default, so `hs relay`,
  `hs cluster` and `hs server` read every value the same way). A `UnitError` is re-raised as
  `ArgumentError(f'autoscale.period: {error}')`, so it exits 2 with `-W`'s message naming the key.
- `RelayApp.check_args` reads it, which raises before the relay binds or locks its database;
  `RelayApp.run` passes `period=self.site_period` to `RelayThread`.
- `relay/site.py`: `DEFAULT_SITE_PERIOD` reads the built-in `default.autoscale.period`, as
  `cluster/remote.py:328` does, so importing the module (every `hs` command imports it) never parses
  user configuration. Its docstring says the relay passes the configured period.

### Launched client command line — `src/hypershell/cluster/launch.py` (R6)

`client_options` keeps `'-w', str(bundlewait)` and `'-S', str(task_signalwait)` (now `int`s, bare
whole seconds every client back to 2.9.0a4 reads with `type=int`). `-d` is written through a small
formatter that gives a plain decimal and never an exponent (`format(Decimal(repr(v)), 'f')`):
`-600.0`, `0.5`, `0.00001`, never `1e-05`. Every value that prints without an exponent today prints
the same. The two-word shape stays: `-600.0` matches argparse's negative-number rule on every
supported Python. `relay/site.py:172` passes the built-in `DEFAULT_DELAY`/`DEFAULT_SIGNALWAIT`
(ints) and is unchanged. §11: still the one builder, still only 2.9.0a4 options.

### Help, docs, completions (R7)

- `APP_HELP` lines in `submit.py`, `server.py`, `client.py`, `cluster/__init__.py`, `task.py`: each
  option says it takes seconds or a duration (`30s`, `5m`, `1h30m`, a Slurm time), in a7's short
  style; `-Q`'s metavar text `NUM` becomes `SEC`. `-d`'s line names the random form attached
  (`--delay-start=-10m`).
- `docs/_include/{submit,server,client,cluster,task_wait,task_run}_help.rst`: the same, with a7's
  full forms paragraph; `-d`'s paragraph writes the random form attached and says fractions are kept.
  `.. versionchanged:: 2.9.0` goes beside each changed `.. include::` in `docs/cli/{submit,server,
  client,cluster,task_wait,task_run}.rst` (AGENTS.md § Docs: generated snippets are never
  hand-marked).
- `docs/_include/config_param_ref.rst`: `[submit] .bundlewait`, `[task] .signalwait`,
  `[server] .poll`, `[autoscale] .period` say a string takes the option's forms (a7's
  `client.timeout` wording) with `.. versionchanged:: 2.9.0`; `.period` notes `hs relay` reads it
  too. `[client] .bundlewait`, `.heartrate`, `server.evict` and the `[relay]` keys are not touched
  (non-goals).
- `docs/getting_started.rst:101-110`: the `--delay-start` sentence says the value may carry units
  (the example already writes `--delay-start=-60`).
- Completions (`share/zsh/site-functions/_hs`, `share/bash_completion.d/hs`): zsh descriptions state
  the forms (no `[`, `]` or `:` in the text); candidates keep today's numbers and add unit forms;
  `-d` keeps `-60 -600` and never offers a signed unit form. bash has no description field, so its
  candidates carry the forms ([03](research/03-poll-and-completions.md)).
- `share/man/` is not edited (rebuilt at release).

### Decisions on the GOAL's plan-stage questions (for sign-off at the plan gate)

- **D1 — Spellings only `float()` took are refused on `--delay-start`.** `+5`, `.5`, `5.`, `1e3`,
  `inf`, `nan`, `1_000` were accepted by `type=float`. R5 keeps "every value accepted today", R4
  refuses anything "not a duration in full"; the GOAL's own R5 examples (`30`, `0.5`, `-600`,
  `--delay-start=-600`) all stay. One grammar wins, as a7's review dropped `-W +60` and `-W 1_000`.
  The user-facing note names them. *Alternative:* also accept `.5` and `5.` on `-d` only.
- **D2 — The delay's error is `-W`'s plus one clause** for the random form (above).
- **D3 — Whole-second options round fractions up**, `-Q` included (`-Q 0.5` → 1). Today `type=int`
  refuses every fraction, so nothing that works changes ([03](research/03-poll-and-completions.md)).
- **D4 — `hs relay` converts in `RelayApp`**, numbers pass unchanged, and `DEFAULT_SITE_PERIOD` reads
  the built-in default ([02](research/02-config-and-relay-period.md)).
- **D5 — The launched delay is a plain decimal**, never an exponent (R6, above).
- **D6 — Completions:** bash meets R7 through unit-form candidates, since it has no descriptions;
  no signed unit form is ever a candidate.
- **D7 — Tests go in a new `tests/test_duration_options.py`**. The argparse-dependent cases also run
  under Python 3.11 in a separate environment (cheap: about 10 s to build). The two-word
  `-d -10m` case is marked to run only on Python 3.14+, and the attached form runs everywhere.
- **D8 — User-facing note** (drafted by `hs-publish` into the squash body): the new forms; signed
  values on `-w`/`-S`/`-P`/`-Q`/`-n` are refused; on `--delay-start`, `+`, exponent, `inf`/`nan` and
  leading/trailing-dot spellings are refused (D1); write the random form attached on Python 3.11–3.13.

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1 | `type=parse_time` on the eleven `-w`/`-S`/`-P`/`-Q`/`-n` declarations |
| R2 | `parse_delay` (leading `-`, a7 matcher, unrounded `float`) on `-d` of `hs client`/`hs cluster`; `wait_start` unchanged |
| R3 | Argparse string defaults through the new `type=`; `RelayApp.site_period` for `hs relay`; `DEFAULT_SITE_PERIOD` off user config |
| R4 | Full-match `parse_time`/`parse_delay`; `UnitError` message (`-W`'s, plus the delay clause); `ArgumentError` naming `autoscale.period`; exit 2 |
| R5 | Bare integers read as seconds by `parse_time`; `float(Fraction)` equals `float()` for every bare decimal; config numbers skip `type=` and the relay's number pass-through |
| R6 | `client_options`: `str(int)` for `-w`/`-S`, plain-decimal `-d`; contract test unchanged |
| R7 | `APP_HELP`, `_include/*_help.rst`, `config_param_ref.rst`, `getting_started.rst`, zsh and bash completions, `versionchanged:: 2.9.0` |
| R8 | `tests/test_duration_options.py` (below), run on 3.14 and 3.11 |

## 3. Invariant gate (AGENTS.md constitution check)

Checked before research and again after this design was drafted.

- **§10 Configuration** — `config` is never mutated: values are converted where they are read (an
  argparse default, `RelayApp.site_period`). `0` keeps meaning "no limit/none" where it did; the new
  parsers return `0` for `0`. The relay's database rebinding at import is not touched.
- **§11 Cluster orchestration** — `client_launch()`/`client_options` stays the one builder. It emits
  only options a 2.9.0a4 client accepts, with values that client's `type=int`/`type=float` read
  (bare `int`s, a plain-decimal `float`). The key is never on an argv. The relay's site launches are
  unchanged. The autoscaler and `SiteScaler` logic is unchanged; only the period's source is.
- **§8 Signals (relay carve-out)** — untouched: the new check runs in `check_args`, before handlers
  are installed.
- **§5 Concurrency / §6 shutdown** — untouched: no FSM, thread or queue code changes. The poll value's
  type stays `int` from the option.
- **§12 Conventions** — help snippets, completions and prose change in the same commit as each CLI
  change; `versionchanged:: 2.9.0` beside includes and on config entries. Python 3.11–3.14 is
  supported, with no shims (the 3.11–3.13 argparse behaviour is documented, not worked around). New
  tests are tagged `@mark.unit`/`@mark.integration` and use `@mark.parametrize`. Exit codes come
  from `exit_status`/`ArgumentError`. Docs name only top-level commands and never pass a key with `-k`.
- **High-blast-radius files:** `client.py` and `server.py` change only at option declarations, the
  `core.types` import line and `APP_HELP`. No logic in either changes.

### Deviation justifications

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| — | — | — |

## 4. Rabbit holes (resolved)

- `--delay-start`'s sign versus argparse → the sign lives in the value, so the parser takes it. The
  two-word `-d -10m` is a GOAL non-goal on 3.11–3.13, confirmed by probe; bare negative numbers still
  work as two words everywhere ([01](research/01-delay-start.md)).
- The relay's import-time read → converting at import would turn a bad value into a traceback in
  every `hs` command; converting in `RelayApp` does not ([02](research/02-config-and-relay-period.md)).
- `str(float)` exponents on the launched command line → a plain-decimal formatter
  ([01](research/01-delay-start.md)).
- Other string paths to the autoscalers → none ([02](research/02-config-and-relay-period.md)).

## 5. Risks & open questions

- **D1 needs the foreman's ruling** (refuse or keep `.5`/`5.` on `-d`). The build follows the ruling.
- On Python 3.11–3.13 a signed *unit* value given as two words (`-w -5m`, `-d -10m`) fails in argparse
  with "expected one argument" rather than the forms message. It still exits 2, as `-W -5m` does
  today. Refusal tests use the attached spelling or bare signed numbers (`-w -5`), which reach `type=`
  everywhere.
- A TOML *float* in `submit.bundlewait` (`30.5`) still skips `type=` and is forwarded by `hs cluster`
  as `-w 30.5`, which the `type=int` of a client from an earlier release refuses. This is
  pre-existing and not in R6 (which is about values the options parse). Left as is.
- `DEFAULT_SITE_PERIOD`'s value changes for a library caller who relied on it carrying user
  configuration. It has no in-tree caller besides `RelayThread`'s default, and no docs reference it.

## 6. Verification strategy

- **Parser tables** (`tests/test_duration_options.py`, unit): every R1 declaration through its
  app's `interface.parse_args` with a unit form, a Slurm form, a bare integer, a refusal (`5MB`,
  `1hr`) and a signed refusal (`-5`, `+60`); `-d` with fixed and random forms, fractions kept
  (`1.5s` → 1.5, `--delay-start=-1:30` → -90.0, `-0.5` → -0.5), R5's bare numbers bit-identical to
  `float()`, D1's spellings refused, refusal messages starting with `-W`'s and naming the random form;
  two-word `-d -10m` only on 3.14+.
- **Random delay** (unit): `ClientThread.wait_start` with stubbed `time.sleep`/`random.uniform`, or
  a seeded draw with a short bound: `-2s` draws in `[0, 2]`, `2s` sleeps 2.0, `0.5` sleeps 0.5.
- **Config/env** (integration, `temp_site`): `HYPERSHELL_AUTOSCALE_PERIOD=5m`,
  `HYPERSHELL_SUBMIT_BUNDLEWAIT=30s`, `HYPERSHELL_TASK_SIGNALWAIT=5s`, `HYPERSHELL_SERVER_POLL=1m`
  and the same as TOML strings in the site `config.toml` give each command's parsed value in seconds;
  a bad string exits 2 naming the value.
- **Relay** (unit + integration): `site_period` for `'5m'` → 300 and for numbers unchanged, and the
  value reaches `RelayThread` (start-up harness in `tests/test_relay_launch.py`);
  `HYPERSHELL_AUTOSCALE_PERIOD=5MB hs relay` exits 2 naming `autoscale.period` and the value.
- **Launch** (unit): `ClusterApp` args `--delay-start=-10m -w 1m -S 5s` → `client_options` words
  `-d -600.0 -w 60 -S 5`, read back by `int`/`float` (the 2.9.0a4–a8 client types) and by today's
  `ClientApp.interface`; the formatter never emits an exponent. The existing contract tests in
  `tests/test_cluster_launch.py` stay green.
- **CLI drives** (`temp_site.sh`): the GOAL's evidence commands (`hs submit -w 1m`, `hs cluster t.in
  -N1 -w 30s -S 5s -P 2m -Q 1m --delay-start=-2s`, `hs wait -n 1s`, `hs run -n 1s` against a
  `--capture` cluster, `--period 5MB` exiting 2).
- **Pythons:** the new test file under 3.14 (`uv run`) and 3.11 (`UV_PROJECT_ENVIRONMENT=$TMPDIR/venv311
  uv run --python 3.11`). **Docs:** the Sphinx `-W` build (sandbox off).

---

*Backing research: [`research/00-digest.md`](research/00-digest.md).*

## Plan-gate rulings (2026-11-14)

Foreman, gate judge under the maintainer's mandate. Approved as planned.

- **D1:** one grammar. `-d` refuses `+5`, `.5`, `5.`, `1e3`, `inf`, `nan` and `1_000`, as `-W` does.
  The user-facing note lists them.
- **D2–D8:** approved as proposed. That covers the error clause, round-up on the whole-second
  options, the relay converting the period in `RelayApp` with `DEFAULT_SITE_PERIOD` at the built-in
  default, the plain-decimal launched delay, bash candidates, the new test file with a 3.11 run, and
  the note's contents.
- The temp-site drives run one at a time (port 50001).

## Review-cycle-1 ruling (2026-11-14)

Foreman, gate judge under the maintainer's mandate. On **F1** (underscore-grouped integers such as
`-w 1_000`): plan-gate D1's "one grammar with `-W`" covers the whole-second options as well as
`-d`. R4 governs, and F1 is not a defect. The user-facing note names underscore-grouped numbers
next to signed values. The verdict stands at approved. After review, `develop` (#105) was merged in
to resolve one `docs/cli/server.rst` conflict, keeping both `versionchanged` blocks. The merge brings
in only #105's reviewed changes.
