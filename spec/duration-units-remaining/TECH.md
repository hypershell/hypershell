---
slug: duration-units-remaining
title: Every duration option takes the forms the timeouts take
kind: fix
appetite: small
status: done
branch: fix/duration-units-remaining
base: develop
current_phase: done
last_updated: '2026-11-14'
phases:
- id: P1
  name: '--delay-start: signed, unrounded parse_delay end to end (options, launch
    line, docs)'
  status: done
  satisfies:
  - R2
  - R4
  - R5
  - R6
  - R7
  - R8
  depends_on: []
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run pytest -q tests/test_duration_options.py tests/test_size_duration.py
    tests/test_cluster_launch.py && UV_PROJECT_ENVIRONMENT=$TMPDIR/venv311 uv run
    --python 3.11 pytest -q tests/test_duration_options.py && .agents/factory/bin/temp_site.sh
    sh -c 'echo "echo a" > t.in && uv run hs cluster t.in -N1 --delay-start=-2s &&
    uv run hs cluster t.in --no-db -N1 -d 0.5s && uv run hs client --help | grep -q
    -e "--delay-start=-10m" && { uv run hs cluster t.in --no-db -N1 --delay-start=1hr
    2> err; test $? -eq 2; } && grep -q "1hr" err && grep -q -e "--delay-start=-10m"
    err' && UV_PROJECT_ENVIRONMENT=$TMPDIR/docs-venv uv run --python 3.12 --group
    docs sphinx-build -W --keep-going -q -E -b html docs $TMPDIR/docs-html
- id: P2
  name: Whole-second options -w, -S, -P, -Q, -n and their four keys through parse_time
    (help, docs, completions)
  status: done
  satisfies:
  - R1
  - R3
  - R4
  - R5
  - R6
  - R7
  - R8
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run pytest -q tests/test_duration_options.py tests/test_size_duration.py
    tests/test_cluster_launch.py tests/test_submit.py && UV_PROJECT_ENVIRONMENT=$TMPDIR/venv311
    uv run --python 3.11 pytest -q tests/test_duration_options.py && .agents/factory/bin/temp_site.sh
    sh -c 'echo "echo a" > t.in && uv run hs submit -w 1m echo b && uv run hs cluster
    t.in -N1 -w 30s -S 5s -P 2m -Q 1m --delay-start=-2s && uv run hs wait -n 1s "$(uv
    run hs list id -f plain | head -1)" && { uv run hs run -n 1s -- echo ran > run.out
    2>&1 & p=$!; until uv run hs list args -f plain | grep -q "echo ran"; do sleep
    0.2; done; echo "echo c" | uv run hs cluster -N1 --capture; wait $p; } && grep
    -q ran run.out && HYPERSHELL_AUTOSCALE_PERIOD=5m HYPERSHELL_SUBMIT_BUNDLEWAIT=30s
    HYPERSHELL_TASK_SIGNALWAIT=5s HYPERSHELL_SERVER_POLL=1m uv run hs cluster t.in
    --no-db -N1 && printf "[autoscale]\nperiod = \"5m\"\n[submit]\nbundlewait = \"30s\"\n[task]\nsignalwait
    = \"5s\"\n[server]\npoll = \"1m\"\n" > config.toml && uv run hs cluster t.in --no-db
    -N1 && rm config.toml && { uv run hs cluster t.in --no-db -N1 --period 5MB 2>
    err; test $? -eq 2; } && grep -q "5MB" err && { uv run hs cluster t.in --no-db
    -N1 --bundlewait=-5 2> err; test $? -eq 2; }' && UV_PROJECT_ENVIRONMENT=$TMPDIR/docs-venv
    uv run --python 3.12 --group docs sphinx-build -W --keep-going -q -E -b html docs
    $TMPDIR/docs-html
- id: P3
  name: hs relay reads autoscale.period through parse_time (RelayApp, not at import)
  status: done
  satisfies:
  - R3
  - R4
  - R5
  - R8
  depends_on:
  - P2
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run pytest -q tests/test_duration_options.py tests/test_relay_launch.py
    tests/test_relay_topology.py && UV_PROJECT_ENVIRONMENT=$TMPDIR/venv311 uv run
    --python 3.11 pytest -q tests/test_duration_options.py && .agents/factory/bin/temp_site.sh
    sh -c '{ HYPERSHELL_AUTOSCALE_PERIOD=5MB uv run hs relay 2> err; test $? -eq 2;
    } && grep -q "autoscale.period" err && grep -q "5MB" err && HYPERSHELL_AUTOSCALE_PERIOD=5MB
    uv run hs list --count > /dev/null' && UV_PROJECT_ENVIRONMENT=$TMPDIR/docs-venv
    uv run --python 3.12 --group docs sphinx-build -W --keep-going -q -E -b html docs
    $TMPDIR/docs-html
review:
  last_reviewed_commit: 21c868f996d5904dc30e41e48f27c42c0a6fc41a
  verdict: approved
  blocked_reason: ''
  cycle: 1
---
# TECH.md — Every duration option takes the forms the timeouts take

The **context engine and finite-state machine** for building this fix. The YAML frontmatter above is
the resume ground-truth (read it with
`uv run python .agents/factory/bin/next_phase.py spec/duration-units-remaining/TECH.md`); the per-phase
checklists below are the work. `hs-build` executes the next actionable phase, runs its `verify:`
command, updates state via `uv run python .agents/factory/bin/set_phase.py …`, and makes one atomic
code+state commit.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md) — R-IDs are the contract.
- **Authoritative design:** [`PLAN.md`](PLAN.md) (§2 has decisions D1–D8; build to the foreman's
  ruling on D1).
- **Backing research:** [`research/00-digest.md`](research/00-digest.md) + briefs 01–03.

## Frontmatter field reference

- `status` (top): `planned | in_progress | blocked | in_review | done`.
- `appetite`: `small | big` — caps phase count and build-iteration budget (circuit breaker).
- phase `status`: `pending | in_progress | done | blocked`
- `satisfies`: GOAL R-IDs this phase delivers (traceability anchor for `hs-review`).
- `depends_on`: phase ids that must be `done` first.
- `parallel`: `false` here — P1 and P2 touch `client.py`/`server.py`, and all three share the new test
  file and `config_param_ref.rst`.
- `hammerable`: `false` — correctness phases.
- `hill`: `uphill` → `crest` → `downhill`.
- `attempts`: durable failed-verify counter, bumped by `set_phase.py --phase P<n> --record-attempt`.
- `verify`: the exact command that proves the phase.
- `review.cycle`: completed review passes.

## Conventions (apply to every phase)

- [`AGENTS.md`](../../AGENTS.md) is the constitution; footguns in
  [`.agents/factory/invariants.md`](../../.agents/factory/invariants.md) §10, §11, §12.
  **High-blast-radius files touched:** `client.py` and `server.py`. Change only the option
  declarations, the `core.types` import line and `APP_HELP` there; no logic. `ClientThread.wait_start`
  stays as it is.
- One phase per `hs-build` invocation; one atomic commit with the code and the `TECH.md` state change,
  subject `[fix] Build duration-units-remaining P<n>: …`. **No `Co-Authored-By` trailer.**
- A CLI change updates its `APP_HELP`, its `docs/_include/*_help.rst` snippet, its zsh and bash
  completions, and its prose **in the same commit**. `.. versionchanged:: 2.9.0` (one short clause,
  no `:pr:` yet) goes beside each changed `.. include::` in `docs/cli/*.rst` and under each changed
  configuration entry. Do not edit `share/man/`. Write the random delay attached everywhere
  (`--delay-start=-10m`). Never offer a signed unit form as a completion candidate. Keep `[`, `]` and
  `:` out of zsh description text.
- Docs and examples name only top-level commands (never `hs task …`) and never pass a key with `-k`.
- Exit codes come from the existing exception mapping (`ArgumentError` → 2); no integer literals.
- New tests go in `tests/test_duration_options.py`, each tagged `@mark.unit` or `@mark.integration`,
  parametrized with `@mark.parametrize`. Use only spellings that reach `type=` on Python 3.11: the
  attached form for signed unit values (`--delay-start=-10m`, `--bundlewait=-5m`). A two-word
  `-d -10m` case is `skipif(sys.version_info < (3, 14))`.
- **Verify environment.** `uv` needs a writable cache (`UV_CACHE_DIR` under `$TMPDIR`) in a sandbox.
  The 3.11 run builds `$TMPDIR/venv311` on first use (about 10 s). The Sphinx build runs with the
  sandbox off. The CLI drives bind the default port 50001: run them one at a time.

---

## Phase P1 — `--delay-start`: signed, unrounded `parse_delay` end to end
**Satisfies:** R2, R4, R5, R6, R7, R8 (delay) · **Depends on:** —
**Goal:** `hs client` and `hs cluster` read `-d 30s`, `--delay-start=1.5s` (1.5) and
`--delay-start=-10m` / `--delay-start=-1:30` (random up to 600 / 90). They keep every bare number
(`30`, `0.5`, `-600`) bit-identical to `float()`, refuse `1hr`/`5MB` with `-W`'s message plus the
random form, and hand launched clients a plain-decimal delay.

- [x] `core/types.py`: factor `parse_time`'s pattern loop into a private helper returning the exact
      `Fraction` of seconds (or `None`) for a stripped text; `parse_time` = `ceil` of it, unchanged
      behaviour and message. Add `parse_delay(value) -> float` to `__all__`: strip, one optional
      leading `-`, the helper on the rest, `float(±seconds)` unrounded; on no match (or
      `OverflowError`) raise `UnitError` with exactly `parse_time`'s message for the same text plus
      `; a leading - waits a random time up to that duration (e.g., --delay-start=-10m)`. Per the
      foreman's ruling on PLAN D1, either refuse `+5`/`.5`/`5.`/`1e3`/`inf`/`nan`/`1_000` (recommended)
      or admit `.5`/`5.` in `parse_delay` only.
- [x] `client.py` and `cluster/__init__.py`: `-d/--delay-start` `type=float` → `type=parse_delay`;
      add `parse_delay` to the existing `core.types` import line.
- [x] `cluster/launch.py` `client_options`: write the delay as a plain decimal with no exponent
      (`format(Decimal(repr(float(delay_start))), 'f')`, or an equivalent helper); keep the
      two-word `'-d', …` shape and `str()` for every other value.
- [x] Help: the `-d` line in `client.py`/`cluster/__init__.py` `APP_HELP`, and the `-d` paragraph in
      `docs/_include/{client,cluster}_help.rst`. They state the duration forms (a7's paragraph), that
      fractions are kept, and that a leading `-` waits a random time up to that duration, written
      `--delay-start=-10m` (replacing `--delay-start=-600` in `client_help.rst:64-67`, or keeping it
      alongside). Add `.. versionchanged:: 2.9.0` beside the `.. include::` in
      `docs/cli/{client,cluster}.rst`. `docs/getting_started.rst:101-103`: the sentence says the value
      may carry units.
- [x] Completions: zsh `-d` descriptions (`client`, `cluster`) gain the forms and the attached random
      form; zsh and bash `-d` candidates become e.g. `30 60 10m -60 -600` (no signed unit form).
- [x] `tests/test_duration_options.py` (new): `@mark.unit` `parse_delay` accept table (`30`→30.0,
      `0.5`, `-600`, `-0.5`, `30s`, `1.5s`→1.5, `10m`, `-10m`→-600.0, `1:30`, `-1:30`→-90.0, `1-12`,
      `-1h30m`) with each bare number equal to `float(text)`; refusal table (`5MB`, `1hr`, `2hours`,
      `1w`, `30m1h`, `--10m`, `- 10m`, `-`, `''`, plus D1's spellings per the ruling) whose message
      starts with `parse_time`'s message for the same text and contains `--delay-start=-10m`;
      `parse_time` unchanged on a7's table (import from `test_size_duration`, or rely on that file).
      Per app (`ClientApp`, `ClusterApp`): `--delay-start=-10m`, `-d-10m`, `-d -600`, `-d 0.5`,
      `-d 1:30`, `-d 30s` parsed; `-d -10m` two-word only on 3.14+; refusal raises `ArgumentError`
      naming the value. `ClientThread.wait_start` with `time.sleep` and `random.uniform` stubbed:
      `2s` sleeps 2.0, `0.5` sleeps 0.5, `-2s` draws `uniform(0, 2.0)`, `0` does not sleep.
      Launch (R6, `-d` part): `client_options` for `-600.0`, `0.5`, `1e-05`, `1e16`, `0` gives words
      with no `e`, which `float()` reads back exactly and `ClientApp.interface.parse_args` reads as the
      same value.
- **Verify:** see frontmatter. It runs the new tests on 3.14 and 3.11, the a7 and launch-contract
  tests, a `temp_site` drive (`hs cluster` with `--delay-start=-2s` and `-d 0.5s` completes; help shows
  the attached random form; `--delay-start=1hr` exits 2 naming the value and the random form), and the
  Sphinx `-W` build.
- **Touches:** `src/hypershell/core/types.py`, `src/hypershell/client.py`,
  `src/hypershell/cluster/__init__.py`, `src/hypershell/cluster/launch.py`,
  `docs/_include/{client,cluster}_help.rst`, `docs/cli/{client,cluster}.rst`,
  `docs/getting_started.rst`, `share/zsh/site-functions/_hs`, `share/bash_completion.d/hs`,
  `tests/test_duration_options.py`.

## Phase P2 — Whole-second options and their four keys through `parse_time`
**Satisfies:** R1, R3, R4, R5, R6, R7, R8 (whole-second options) · **Depends on:** P1
**Goal:** `-w` (`hs submit`, `hs server`, `hs client`, `hs cluster`), `-S` (`hs client`,
`hs cluster`), `-P` and `-Q` (`hs server`, `hs cluster`) and `-n` (`hs wait`, `hs run`) read every a7
form. So do the keys `submit.bundlewait`, `task.signalwait`, `autoscale.period` and `server.poll`
(as TOML strings or environment variables) for every command that reads them through an option.
Signed values and non-durations are refused, a bare integer stays seconds, and a launched client gets
bare whole seconds.

- [x] `type=int` → `type=parse_time` on: `submit.py` `-w`; `server.py` `-w`, `-P`, `-Q`; `client.py`
      `-w`, `-S`; `cluster/__init__.py` `-w`, `-S`, `-P`, `-Q`; `task.py` `-n` (both declarations; add
      `parse_time` to `task.py`'s `core.types` import). Defaults, dests and metavars unchanged.
- [x] Help: the `-w`, `-S`, `-P`, `-Q`, `-n` lines of each `APP_HELP` (`submit.py`, `server.py`,
      `client.py`, `cluster/__init__.py`, `task.py` for `hs wait` and `hs run`) state the forms in a7's
      short style; `-Q`'s `NUM` becomes `SEC`. The same options' paragraphs in
      `docs/_include/{submit,server,client,cluster,task_wait,task_run}_help.rst` get a7's forms
      paragraph. `.. versionchanged:: 2.9.0` beside the `.. include::` in
      `docs/cli/{submit,server,task_wait,task_run}.rst` (`client`/`cluster` already marked in P1 —
      extend that clause rather than add a second marker).
- [x] `docs/_include/config_param_ref.rst`: `[submit] .bundlewait`, `[task] .signalwait`,
      `[server] .poll`, `[autoscale] .period` — "A string takes the same forms as `-w`/`--bundlewait`
      …" (a7's `client.timeout` wording) and `.. versionchanged:: 2.9.0`. Leave `[client] .bundlewait`,
      `.heartrate`, `server.evict` and every `[relay]` key alone (non-goals).
- [x] Completions: zsh descriptions of `-w`, `-S`, `-P`, `-Q`, `-n` on every command state the forms;
      zsh and bash candidates add unit forms (e.g. `-w`: `5 30s 1m 10m`; `-S`: `1 5 10 30s`;
      `-P`: `30 60 5m 10m`; `-Q`: `1 5 30 1m`; `-n`: `5 10 30 1m`).
- [x] `tests/test_duration_options.py`: `@mark.unit` table over every (app, option, dest) of R1
      (13 entries counting `hs wait` and `hs run` separately) × (`30s`→30, `5m`→300, `1h30m`→5400,
      `1.5h`→5400, `1:30`→90, `1:30:00`→5400, `1-12`→129600, `90`→90, `0.5`→1). Refusals: `5MB`,
      `1hr`, `2hours`, `1w`, `30m1h`, plus signed `-5` (two words) and `+60` / `--opt=-5m` (attached),
      each raising `ArgumentError` with the value and `1h30m` in the message.
      `@mark.integration` (`temp_site`): for each key, the environment string (`HYPERSHELL_AUTOSCALE_PERIOD=5m`,
      `HYPERSHELL_SUBMIT_BUNDLEWAIT=30s`, `HYPERSHELL_TASK_SIGNALWAIT=5s`, `HYPERSHELL_SERVER_POLL=1m`)
      and the same as a TOML string in the site `config.toml` give the parsed default in seconds for
      every app that reads it. Defaults are captured when the app modules are imported, so read them in
      a subprocess (`tests.main` runs one): e.g. `python -c` printing
      `ClusterApp.interface.parse_args(['t.in']).autoscaling_period` under the env/site. A TOML or env
      *number* keeps its value and type. Launch (R6): `ClusterApp` parsed from
      `--delay-start=-10m -w 1m -S 5s` → `client_options` holds `-w 60 -S 5 -d -600.0`, each read by
      `int`/`float` and by `ClientApp.interface`.
- **Verify:** see frontmatter. It runs the tests on 3.14 and 3.11 with the a7, submit and
  launch-contract tests, then the GOAL's evidence drive in a `temp_site`: `hs submit -w 1m`;
  `hs cluster -N1 -w 30s -S 5s -P 2m -Q 1m --delay-start=-2s`; `hs wait -n 1s`; `hs run -n 1s`
  against a `--capture` cluster; all four env strings; all four TOML strings; `--period 5MB` and
  `--bundlewait=-5` exit 2. Then the Sphinx `-W` build.
- **Touches:** `src/hypershell/{submit,server,client,task}.py`, `src/hypershell/cluster/__init__.py`,
  `docs/_include/{submit,server,client,cluster,task_wait,task_run}_help.rst`,
  `docs/_include/config_param_ref.rst`, `docs/cli/{submit,server,client,cluster,task_wait,task_run}.rst`,
  `share/zsh/site-functions/_hs`, `share/bash_completion.d/hs`, `tests/test_duration_options.py`.

## Phase P3 — `hs relay` reads `autoscale.period` through `parse_time`
**Satisfies:** R3, R4, R5, R8 (relay) · **Depends on:** P2
**Goal:** with `autoscale.period = "5m"` (or `HYPERSHELL_AUTOSCALE_PERIOD=5m`) the relay's site fleet
checks every 300 s, as `hs cluster` does. A bad string stops `hs relay` with exit 2, naming
`autoscale.period`, the value and the forms, before it touches its database. No other command is
affected by the value at import.

- [x] `relay/__init__.py` `RelayApp`: cached property `site_period` — `config.autoscale.period`; a `str`
      through `parse_time`, anything else unchanged; a `UnitError` re-raised as
      `ArgumentError(f'autoscale.period: {error}')`. Read it in `check_args` (after the existing
      checks, before `check_bounds` returns); pass `period=self.site_period` to `RelayThread` in `run`.
- [x] `relay/site.py`: `DEFAULT_SITE_PERIOD` reads `default.autoscale.period` (import `default` beside
      `config`); docstring: the built-in default, the relay passes the configured period.
- [x] `docs/_include/config_param_ref.rst` `[autoscale] .period`: one sentence that `hs relay` reads it
      as its site fleet's period, in the same forms (inside P2's `versionchanged` clause, no second
      marker).
- [x] `tests/test_duration_options.py` (or `tests/test_relay_launch.py` beside the start-up order
      tests): `@mark.unit` `RelayApp.from_cmdline([])` with `config.autoscale.period` monkeypatched to
      `'5m'` → `site_period == 300`; `'1:30'` → 90; `60` and `0.5` unchanged; `'5MB'`/`'1hr'` →
      `ArgumentError` naming `autoscale.period` and the value. Through the start-up harness, the
      `period` that reaches `RelayThread` is 300. `@mark.integration`:
      `HYPERSHELL_AUTOSCALE_PERIOD=5MB hs relay` exits 2 with the key and value in stderr, and
      `HYPERSHELL_AUTOSCALE_PERIOD=5MB hs list --count` still runs (import never parses it).
- **Verify:** see frontmatter. It runs the new and relay tests on 3.14 and the new tests on 3.11, a
  `temp_site` drive (`hs relay` refuses `5MB` with exit 2 naming the key; `hs list --count` unaffected), and
  the Sphinx `-W` build.
- **Touches:** `src/hypershell/relay/__init__.py`, `src/hypershell/relay/site.py`,
  `docs/_include/config_param_ref.rst`, `tests/test_duration_options.py` (or
  `tests/test_relay_launch.py`).

---

## How `hs-build` drives this

1. `next_phase.py` prints the next actionable phase.
2. Pre-flight: clean tree, on `branch`, `base` reachable.
3. Execute every `[ ]` in the phase (consult `PLAN.md` / `research/` for detail).
4. Run the phase's `verify:` command — never advance on a checkbox alone.
5. Amend this file if reality diverges (note it in the commit body). STOP and escalate only on a
   **`GOAL.md` contradiction**.
6. Mark the phase `done`, advance `current_phase`, `--touch`; one `[fix]` commit; stop and report.

At publish, the squash commit body carries the user-facing note (PLAN D8): the new forms; signed
values refused on the whole-second options; the `--delay-start` spellings D1 refuses; the attached
random form on Python 3.11–3.13.
