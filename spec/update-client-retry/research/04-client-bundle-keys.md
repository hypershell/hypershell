# 04 — `hs client` reads `client.bundlesize` / `client.bundlewait` (R3, R4, R9 part)

Tree: `fix/update-client-retry` at `eb2a4487` (develop with #117 merged). Line numbers are this tree's.

## 1. Map

- **Defaults** (`core/config.py`): `submit.bundlesize = 1`, `submit.bundlewait = 5` (:137-138);
  `client.bundlesize = 1`, `client.bundlewait = 5` (:173-174). Same values, so the help's
  `{DEFAULT_BUNDLEWAIT}` (already `default.client.bundlewait`, `client.py:345`) is unchanged.
- **Kinds** (`core/config_schema.py:85,88`): `client.bundlesize: I`, `client.bundlewait: D`,
  `submit.bundlesize: I`, `submit.bundlewait: D`. No schema change needed.
- **`advertised_size(value, base=config)`** `client.py:349-361`: explicit `value` wins; else
  `base.which('submit','bundlesize') not in (None,'default')` → `base.submit.bundlesize`; else None.
  Its only caller is `ClientApp.run` `:1720` (runtime, after `check_args`), exported in `__all__` :87.
- **`ClientApp`** `:1644-1648`: `-b` `type=int, default=None`; `-w` `type=parse_time,
  default=config.submit.bundlewait` (class body, i.e. import time). `-R` (#117) is :1658-1661, `-T/-W`
  :1656-1660; `heartrate` read in `check_args` via `config_seconds('client','heartrate')` :1764.
  HELP lines :1597-1598 (`-b … (default: server's)`, `-w … (default: {DEFAULT_BUNDLEWAIT})`).
- **Flow:** `run_client(bundlesize=advertised, bundlewait=…)` → `ClientThread.__init__` :1336
  `advertised, bundlesize = bundlesize, bundlesize or DEFAULT_BUNDLESIZE`; `advertised` → `ClientScheduler`
  (request JSON `'size'`, :245), `bundlesize`/`bundlewait` → inbound/outbound queue sizes (:1345-1346) and
  `ClientCollector` (:444-450). So returning the configured value from `advertised_size` already gives
  R3's "return results in bundles of that size, as an explicit `-b` does" — no `ClientThread` change.
- **Duration forms for `-w`:** argparse applies `type=parse_time` to a *string* default (`"30s"`, an
  `_env`/`_eval` result), and leaves a number as given (int or float, not rounded). Same path for
  `client.bundlewait` as for `submit.bundlewait` today, so "same duration forms" (R4) is just the key
  swap: `bundlewait: int = config.client.bundlewait`. A bad string exits 2 through argparse naming
  `-w` (as `test_seconds_key_refused` pins for `hs cluster` today).

### The "set above the built-in default" mechanism — and a defect in it

`Configuration.which` (cmdkit) raises `KeyError` when a higher layer holds `bundlesize_env` or
`bundlesize_eval`: the merge displaces `bundlesize`, so `which(…, 'bundlesize')` is "Not found".
Probed in process (`Configuration(default=default, local=…)`):

| layer | `which('…','bundlesize')` | attr value | `advertised_size(None)` today |
|---|---|---|---|
| env `client.bundlesize=7` | `env` | `7` | `None` |
| env `submit.bundlesize=9` | `default` for client | — | `9` |
| file `submit.bundlesize_env="XX"`, `XX=6` | **KeyError** | `'6'` (str) | **raises KeyError** |
| file `client.bundlesize_env="XX"` | KeyError | `'6'` (str) | `None` |
| file `client.bundlesize_eval="echo 4"` | KeyError | `'4'` (str) | `None` |

Live on this tree: `[submit] bundlesize_env = "XX"` makes `hs client` die with
`CRITICAL … KeyError: "Not found: ('submit', 'bundlesize')"` before connecting. So a naive swap
`'submit'→'client'` would carry that crash to `client.bundlesize_env/_eval`, which R3 names explicitly.
Two requirements for the new detector:

1. Check each form (`bundlesize`, `bundlesize_env`, `bundlesize_eval`) with `which`, catching
   `KeyError`, as `config_seconds` picks its form (`config.py:357`: `next(given for given in (name,
   f'{name}_env', f'{name}_eval') if given in table)`); set above default iff any form's source is not
   `'default'`.
2. Convert the resolved value: `_env`/`_eval` give **text** (`'6'`). A string `'size'` is refused by the
   server (`core/dispatch.py:139` → `BadRequest "'size' must be a number"`). Read it with
   `config_schema.read_value(Kind.INT, …)` (or `read_number` + whole check) and raise
   `ConfigurationError` naming `client.bundlesize` and `blame(base, 'client', form)` on failure
   (exit `bad_config`, mapped in `core/exceptions.py:139`), the `config_seconds` precedent. An int from
   `HYPERSHELL_CLIENT_BUNDLESIZE` is already converted at import (kind `I`, exits 3 on `abc`).
   Ranges (`0`, negative) are not this cycle's: `-b 0` behaves the same today and bounded-inputs
   (seed `eb705ed4`) owns ranges — rubric "Absurd input is not a finding".

Keep the read in `run()` (as now), not the class body, so an `_eval` runs only for `hs client`, not at
import of `hypershell.client` by `hs cluster`/`hs server`. A tiny generic helper in `core/config.py`
(e.g. `set_above_default(*varpath, base)` returning the form or None) is reusable but optional.

## 2. Other callers (Non-goal: unchanged)

- `cluster/launch.py:525-526` `client_options` always emits `-b str(bundlesize)` and
  `-w plain_seconds(bundlewait)` → every launched client (remote/ssh/autoscale, relay site fleets via
  `relay/site.py:151,183` → `client_launch`) has explicit values; its own `client.*` keys never apply.
- `hs cluster` `-b` = `config.server.bundlesize` (`cluster/__init__.py:153`), `-w` =
  `config.submit.bundlewait` (:156) — keep (Non-goal).
- `LocalCluster` (`cluster/local.py:246-249`) builds `ClientThread(bundlesize=bundlesize,
  bundlewait=bundlewait)` from its own args (defaults `server.DEFAULT_BUNDLESIZE`,
  `submit.DEFAULT_BUNDLEWAIT`); never touches `ClientApp` or `advertised_size`. Remote/SSH/autoscale
  constructors likewise. `hs relay` passes `config.relay.bundlesize`/`relay.bundlewait` (`relay/__init__.py:1169-1170`).
- `hs server -w` = `config.submit.bundlewait` (`server.py:1844`). `submit.bundlesize` is read only by
  `hs submit` (`submit.py:1352`); `submit.bundlewait` by `hs submit` (:1355), `hs server` and
  `hs cluster`. **R9 says "`[submit]`'s keys by `hs submit` and `hs server`"**; the accurate sentence
  is: `.bundlesize` by `hs submit`; `.bundlewait` by `hs submit`, `hs server` and `hs cluster` (which
  hands it to its clients as `-w`). Flag for the plan.

## 3. Probe (observables for verify)

Live (`temp_site.sh`, server `-b 100 --no-tls` on a free port, key via `HYPERSHELL_SERVER_AUTH`,
40× `echo` + `sleep 16`, client `-N4 -T 5`, `HYPERSHELL_LOGGING_LEVEL=TRACE`):

```
client vars (BUNDLESIZE=7, BUNDLEWAIT=11): received sizes {41,1}; first "Bundle wait exceeded (0:00:05.03)"
submit vars (BUNDLESIZE=9, BUNDLEWAIT=13): received sizes {9,5,1}; first "Bundle wait exceeded (0:00:13.10)"
```
Observables: DEBUG `Received (\d+) tasks` (regex already in `tests/test_dispatch_workload.py:34`
`RECEIVED`) for the advertised size; the first TRACE `Bundle wait exceeded (H:MM:SS.ffffff)` ≈ the wait
(it fires once `since_last >= bundlewait` even with nothing held, so the first line's elapsed ≈ wait).
No `timeout(1)` on this host; `perl -e 'alarm N; exec @ARGV' …` works. Script kept at
`$TMPDIR`-scratch `probe_ucr.sh` (not in repo).

Cheap in-process seam (≈1 s each, child interpreter so `config` is rebuilt):
```
.agents/factory/bin/temp_site.sh env HYPERSHELL_CLIENT_BUNDLESIZE=7 HYPERSHELL_CLIENT_BUNDLEWAIT=11 \
  uv run python -c 'from hypershell.client import ClientApp, advertised_size; print(advertised_size(None), ClientApp.interface.parse_args([]).bundlewait)'
```
Today: client vars → `None 5`; submit vars (9/13) → `9 13`; `CLIENT_BUNDLEWAIT=30s` → `None 5`. After:
`7 11`, `None 5`, `None 30`. For `_env`: `[client] bundlesize_env = "XX"` in a file with
`HYPERSHELL_CONFIG_FILE` and `XX=6` → `6` (int); `[submit] bundlesize_env` → `None` (today: KeyError).

## 4. Tests that change, and red candidates

All pass on this tree (21 selected, serial). Each below fails on develop, passes after:

- `tests/test_client_request.py:166-176` `test_advertise_only_an_explicit_size`: swap the `submit`
  layers to `client`; `user={'client':{'bundlesize':9}}` flips `None`→`9`; add `submit` layers → `None`;
  add `_env`/`_eval` forms (monkeypatch the variable; `bundlesize_eval='echo 4'`) → int `6`/`4`, and a
  `submit.bundlesize_env` layer → `None` (no KeyError). Unit, cheap.
- `tests/test_duration_options.py:265-266` `SECONDS_KEYS`: drop `ClientApp` from the `submit.bundlewait`
  row; add `('client','bundlewait','30s',[('ClientApp',[],'bundlewait')])`. That row runs through the
  four parametrized tests (env, file, number unchanged — `7` and `0.5` keep type — and refused). Add a
  negative: `HYPERSHELL_SUBMIT_BUNDLEWAIT=13` → `ClientApp` reads `5`.
- `tests/test_dispatch_workload.py:177-192` `test_configured_bundlesize_is_advertised`: env
  `HYPERSHELL_SUBMIT_BUNDLESIZE=3` → `HYPERSHELL_CLIENT_BUNDLESIZE=3` (live, integration; red on develop:
  sizes exceed 3). Optionally a twin with `SUBMIT_…=3` asserting a bundle `> 3`.
- New red for the conversion: a child `hs client` (or `advertised_size` with `bundlesize_env` → `'abc'`)
  raising `ConfigurationError` naming `client.bundlesize`.
- Unaffected: `tests/test_env_config.py:647` (`submit.bundlewait` key reading, `KEPT`),
  `test_duration_options.py:349-359` (explicit `-w` words), `test_config_unread.py:231`,
  `test_client_return.py` (collector unit), `test_zone_dispatch.py` (server side), `test_dispatch.py:71`.

## 5. Docs (R9 part)

- `docs/_include/config_param_ref.rst` `[client]` `.bundlesize` (:423-433): add the advertise rule
  moved from `[submit]` (set here, by `HYPERSHELL_CLIENT_BUNDLESIZE`, or its `_env`/`_eval` forms →
  advertised and returned in bundles of that size; left at the built-in default → no size, server's
  own); name `hs client`; add `.. only:: html` / `.. versionchanged:: 2.9.0` (none there now).
  `.bundlewait` (:435-449): name `hs client`, add the duration-forms sentence copied from
  `[submit].bundlewait` (:533-535), extend its existing 2.9.0 marker (:449) with a second line or add one.
- `[submit]` `.bundlesize` (:509-525): remove the client paragraph (:516-519), say `hs submit` reads
  it; rewrite the `:pr:`78`` marker (:525-526) — that sentence is unreleased, so the 2.9.0 note can
  just say it is read by `hs submit` only. `.bundlewait` (:528-541): name `hs submit`, `hs server`,
  `hs cluster`. Never a body line holding only a ``` ``.name`` ``` term (`test_config_schema.py` parser).
- `docs/_include/client_help.rst:19-32`: lines 23-24 `HYPERSHELL_SUBMIT_BUNDLESIZE` / `submit.bundlesize`
  → `HYPERSHELL_CLIENT_BUNDLESIZE` / `client.bundlesize`; `-w` (:34-47) gains "(default: 5, or
  `client.bundlewait`)" or a sentence. Mirror in `ClientApp` HELP :1597-1598 if wording changes
  (`test_main.py:73` compares HELP to `help_text` only, not to the rst).
- `docs/cli/client.rst`: a new `.. versionchanged:: 2.9.0` ("reads `client.bundlesize`/`bundlewait`,
  no longer `submit.*`") placed with the others (:21-42), before the help include :44. #117 added only
  the `-R` marker (:29-31) there.
- `client_desc.rst:4` ("the server's size when not given") — fine, optionally "when not given or configured".
- `share/` completions: option names and value lists unchanged → no edit. Man pages
  (`share/man/man1/{hs,hsx,hyper-shell}.1:918-921,2819-2820`) are rebuilt only at release
  (`[release] … rebuild man pages`), not in the cycle.
- Lockstep prose: `AGENTS.md:526-528` ("bundlesize/bundlewait are read from `config.submit.*` for CLI
  defaults but `config.client.*` for module defaults") must be rewritten; `ROADMAP.md:549-556` entry;
  `spec/zone-dispatch/TECH.md` N4 is history (leave).
- What #117 changed nearby: `[client].ratelimit` entry and marker, `client_help.rst` `-R` (≈155-157),
  `docs/cli/client.rst` `-R` marker. No conflict with the bundle entries.

## 6. Overlap with #118 (`fix/submit-input-checks`)

`git diff origin/develop...fix/submit-input-checks` (41 files): no hunk in `client.py`,
`config_param_ref.rst`, `client_help.rst` or `docs/cli/client.rst`, and none touching `-b`/`-w` lines
in `cluster/__init__.py` (imports :6, :28-37 and gate code ≥:383) or `server.py`. Shared files are
textual only: `AGENTS.md`, `.agents/factory/invariants.md`, `ROADMAP.md`, `docs/cli/cluster.rst`,
`docs/cli/server.rst` (markers). No conflict for this topic.
