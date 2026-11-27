# Research 04 — R8: `client.ratelimit` under `hs cluster`, and the form of `-R`

Base: `develop` `d9901cf0` (this worktree's `src`). Probes ran in a child interpreter per case under
`temp_site.sh`, parse only, no service started.

## Today (probed)

| Probe | `hs client` | `hs cluster` |
|---|---|---|
| no option, no key | `None` | `None` |
| `-R 5` / `-R 0` / `-R -1` | `5` / `0` / `-1` (int) | same |
| `-R 0.5`, `-R inf`, `-R abc` | exit 2, `invalid int value` | exit 2, same |
| `HYPERSHELL_CLIENT_RATELIMIT=5` / `=0.5` | `5` / `0.5` (the default skips `type=int`) | `None` / `None` (key not read) |
| `HYPERSHELL_CLIENT_RATELIMIT=-2` / `=inf` | `-2` / exit 3 (#113 refuses non-finite) | — |
| `HYPERSHELL_TASK_RATELIMIT=7` | — | `7` |

So `hs client` already runs a fractional configured rate in process. Only the command line refuses
one. A negative rate is "no limit" in effect: `wait_ratelimit` (`client.py:946-951`) computes a
negative minimum interval and never waits.

## 1. The parser for `-R` (both commands)

Sites: `client.py:1616-1619` (`ratelimit = config.client.ratelimit or None`, `type=int`),
`cluster/__init__.py:240-241` (`config.task.get('ratelimit', None)`, `type=int`). Plain `float` is
wrong: `float('inf')`, `float('nan')` and `float('1e400')` all succeed, so `-R inf` would parse
and `1/inf` would mean no limit.

**Recommend** `parse_rate` in `core/types.py` beside `parse_period` (`types.py:163`). It reuses
`read_number` (`core/config_schema.py:197-205`), the same reader #113 uses for `Kind.NUMBER`, so the
command line and the environment accept the same forms. `config_schema` imports nothing from
hypershell, so there is no import cycle.
```python
def parse_rate(value: str) -> int | float:
    """Tasks per second: a whole or decimal number, finite (e.g., '5', '0.5')."""
    if (rate := read_number(str(value))) is None:
        raise UnitError(f'Rate {value!r} is not valid: expected a number')
    return rate
```
- `UnitError` is an `ArgumentTypeError` (`types.py:88`), so `-R abc`, `-R inf`, `-R nan` and `-R ''`
  exit 2 with `argument -R/--ratelimit: Rate 'inf' is not valid: expected a number`. The status
  is the same as today's; only the wording changes.
- A whole word stays an `int` (`-R 5` → `5`), so whole rates behave and print exactly as today.
- **Negative: accept, as today.** `-R -1` parses as `int` today and means no limit. Refusing it is
  a new check (Non-goals: `hs client` is "unchanged except `-R`'s form"). It also opens a trap: a
  negative `client.ratelimit` from configuration never passes through `type=` (argparse converts
  only string defaults), so `hs cluster` would forward `-R -1` to a new client that refuses it.
  *Flag for the foreman:* if negatives are to be refused, refuse them in both places (the option
  and the emission), not on the command line alone.
- Configuration: add no new check (Non-goals). `0` stays `or None`, meaning no limit.
  `ClientThread`'s `if ratelimit is not None` (`client.py:1312`) also turns an explicit `-R 0`
  into `0.0` per thread, which is no limit.

Also change `int` to `float` in the annotations and docstrings: `client.py:698, 1237, 1290, 1405,
1477`; `cluster/__init__.py:240`; `launch.py:193, 505`; `local.py:154, 211`; `remote.py:208, 270,
867, 972`; `ssh.py:193, 259`. Help text: `client.py:1574`, `cluster/__init__.py:114`,
`docs/_include/client_help.rst:155-157`, `cluster_help.rst:358-360`, and the reference entry
`config_param_ref.rst:461-467` (R9: name `hs cluster`).

## 2. What `hs cluster` passes to launched clients (`cluster/launch.py:526-527`)

Today the launch emits `str(ratelimit)`. A float `5.0` from `ratelimit = 5.0` in a file (or
`2.5*2`) would be emitted as `-R 5.0`, which a 2.9.0a4 client refuses (`int('5.0')`).

**Recommend** a `plain_rate` beside `plain_seconds` (`launch.py:476-487`, the precedent for a word
both old and new clients read):
```python
def plain_rate(value: int | float) -> str:
    """A whole rate as an integer word ('5'); any other as a plain decimal ('0.5', never '1e-05')."""
    return str(int(value)) if float(value).is_integer() else format(Decimal(repr(float(value))), 'f')
```
Checked: `5`, `5.0` → `5`; `0`, `0.0` → `0`; `0.5`, `2.5`, `1/3`, `123456.789` round-trip;
`1e-05` → `0.00001`; `1e16` → `10000000000000000`. On 200,000 random finite doubles,
`float(plain_rate(x)) == x` held every time. The output has no exponent, so it also suits a parser
stricter than `read_number`.

**Keep `if ratelimit is not None`.** Today an explicit `hs cluster -R 0` emits `-R 0`, which a
2.9.0a4 client accepts and reads as no limit. It also overrides a client's own `client.ratelimit`,
so it must stay. A configured `0` is already `None`, and nothing is emitted.

**Contract test (§11).** `test_launch_contract_uses_only_known_client_options`
(`tests/test_cluster_launch.py:1002-1020`) checks option **names** only: it calls `next(words)` to
skip each value. `-R 0.5` therefore passes it, and so would `-R 5.0`. The invariant's text ("only
options a 2.9.0a4 client accepts") is met by name, but its purpose is not: a 2.9.0a4 client given
`-R 0.5` exits 2, and the launch then ends in `ClusterLaunchError` (exit 5).

There is also a real regression path, not only a new feature. Today a launched client gets no
`-R`, so it reads `client.ratelimit` from its own configuration (shared file or forwarded
environment) and runs `0.5` without trouble. After R8, `hs cluster` emits `-R 0.5`, and a
2.9.0a4 client on that site fails.

**This is a §11 bend: it needs a deviation row and the foreman's sign-off.** Whole rates, `0` and
unset keep a 2.9.0a4 client working. Only a fractional rate needs a client of this release. The
release note's change 8 should say so ("a fractional rate needs `hs client` 2.9.0 or later on the
launched hosts"). The blast radius is small: 2.9.0a4 is an alpha, and the GOAL's ruled alternative
(refusing a fraction at start) is closed.

## 3. The `hs cluster` default

Default `client.ratelimit = 0` (`core/config.py:179`), `Kind.NUMBER` (`config_schema.py:86`).
**Recommend** `ratelimit: Optional[float] = config.client.ratelimit or None` at
`cluster/__init__.py:240`, the same line as `client.py:1616`, with no `task.ratelimit` fallback
(ruled). Every mode receives it through the one call at `cluster/__init__.py:294`:
- local: `local.py:256` passes it straight to the in-process `ClientThread`, where `ratelimit /
  num_threads` (`client.py:1313`) is float division. A fraction works there today; it is only
  unreachable from the key.
- launcher, MPI, autoscaling: `remote.py:312` and `remote.py:1023` call `client_launch`
  (`launch.py:239`), which calls `client_options`.
- SSH: `ssh.py:308` calls `client_launch`.

## 4. Conflict with PR #115 (`fix/relay-shutdown-tail` @ `c9b41f95`, merge-base `d9901cf0`)

Its hunks in the shared files:
- `client.py`: the bundling hunks (159-491). `@@ -1303,12 @@` changes base lines 1308 and 1311 in
  `ClientThread.__init__` (scheduler and collector take `inflight=`). The help `@@ -1553,7 @@`
  changes line 1556 (`-w`).
- `cluster/__init__.py`: `@@ -84,7 @@` changes line 87 (`-w` help).
- `client_help.rst`: line 44. `cluster_help.rst`: lines 111-112 and 126.

I simulated merges with `git merge-file` (temporary copies, no repository write). R8's edits at
`client.py:1237, 1290, 1477, 1574, 1619` and `cluster/__init__.py:114, 240-241` merge
**cleanly**. **An edit to `client.py:1312-1314` (the per-thread division and its log line)
conflicts**, because line 1312 follows PR #115's changed line 1311. **Recommend: leave
1312-1314 alone.** Float division and `{ratelimit:.2}` already handle fractions. For R6, a change
to the `-r` help at `cluster/__init__.py:89` also merged cleanly against line 87, with one
unchanged line between. The `-R` doc entries (client 155, cluster 358) are far from PR #115's
hunks.

## 5. Proposed tests

Red means the test fails on `d9901cf0` by an assertion. No new API is imported at module level;
`parse_rate` and `plain_rate` are reached through the apps and `client_options`/`client_launch`.

**A. `tests/test_duration_options.py`** (it already imports `ClientApp`, `ClusterApp`,
`ArgumentError`, `client_options`, and has `LAUNCH_ARGS` and `read_defaults`). In process, `@mark.unit`:
- `test_rate_option_takes_number[client|cluster-<word>]`: a helper returns `parse_args(['-R',
  w]).ratelimit`, or `str(error)` on `ArgumentError`, so a failure is an assertion. Red: `0.5` →
  `0.5`, `2.5` → `2.5`, `1e-3` → `0.001`. Guard: `5` → `5` (an `int`), `0` → `0`, `-1` → `-1`.
- `test_rate_option_refused[...]`: guard. `abc`, `inf`, `nan`, `1e400`, `''` give an error naming
  `-R/--ratelimit` on both apps. This catches a later switch to plain `float`.
- `test_launched_rate_word[...]`, modeled on `test_launched_delay_is_plain_decimal` (176-200):
  `client_options(..., **{**LAUNCH_ARGS, 'ratelimit': r})`, then check the word after `-R`. Red:
  `5.0` → `'5'`, `1e-05` → `'0.00001'`. Guard: `5` → `'5'`, `0.5` → `'0.5'`, `0` → `'0'` (explicit
  zero still emitted). For each, `float(word) == r`. For whole `r`, `int(word) == r` (a 2.9.0a4
  client's `type=int`). For all, `ClientApp.interface.parse_args(['-R', word]).ratelimit == r`,
  which is red for fractions.
- `test_launch_omits_unset_rate`: guard. `ratelimit=None` emits no `-R`.

**B. Defaults from configuration.** In `test_duration_options.py` via `read_defaults` (child
interpreter; `main` runs `run(...)` without a timeout, so give the new reads a bounded
`run(..., timeout=60)`). Mark `@mark.unit` (parse only, no service) or `integration` to match the
existing `read_defaults` tests:
- `test_cluster_reads_client_ratelimit[env-5|env-0.5|file-2.5]`: red.
  `HYPERSHELL_CLIENT_RATELIMIT=5` gives `[ClusterApp -R default, ClientApp -R default] == [5, 5]`;
  `0.5` gives `[0.5, 0.5]`; a file `[client]\nratelimit = 2.5` gives `[2.5, 2.5]`.
- `test_cluster_ignores_task_ratelimit`: red. `HYPERSHELL_TASK_RATELIMIT=7` gives `ClusterApp`
  `None` (7 today); a file `[task]\nratelimit = 7` gives the same.
- `test_rate_zero_is_no_limit`: guard. `HYPERSHELL_CLIENT_RATELIMIT=0` gives `[None, None]`.
- `test_cluster_option_overrides_key`: red. Env `5` with `-R 0.5` gives `0.5` on the cluster.

**C. `tests/test_cluster_launch.py`:**
- `test_client_launch_fractional_rate[launcher|ssh]`: guard today, since `str(0.5)` is `'0.5'`.
  It pins every mode: `client_launch(..., ratelimit=0.5)` emits `-R 0.5`, and `ratelimit=5.0`
  emits `-R 5` (red).
- Extend the contract test, or add `test_launch_contract_whole_rate_is_int`: for `ratelimit` in
  `(5, 5.0, 0)` (`0` as explicit), the `-R` value satisfies `int(word)`. Red for `5.0`. Record the
  fractional exception in a comment that cites the deviation row.
- `previous_argv` (`479-502`) needs no change: its `5` still matches `'5'`.

**D. Optional local-mode guard** (`tests/test_cluster.py` or `test_duration_options.py`): build
`LocalCluster(ratelimit=0.5, num_threads=2, ...)` without starting it and check that each
executor's rate is `0.25`. If construction has side effects (`set_resources`, a port), skip this
test: the default tests and `local.py:256` already cover the path.
