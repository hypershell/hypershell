# 03 — The ten R6 duration keys: readers, conversion, rounding, docs

Read-only research on `90ab2fc2` source. Probes: a child interpreter per case, `HYPERSHELL_CONFIG_FILE=`
(or a throwaway TOML for file cases), a throwaway `HYPERSHELL_SITE`; services killed by timeout,
no site client launched.

## 1. Readers today

All ten are plain numbers in `default` (`core/config.py:147,168,204-206,217,223-226`); no option
reads any of them, so no parser runs. Every value reaches the consumer as the loader left it.

| Key | Read at (merged config) | Consumer and what it does | Float OK? |
|---|---|---|---|
| `server.evict` | `server.py:1848,1863,1881` (hs server, every mode); `relay/__init__.py:1166` (site server) | `HeartMonitor.__init__` `server.py:794-798`: `>= 10`, `timedelta(seconds=int(v/10))`, `timedelta(seconds=v)` | yes (`=10.5` → server listens) |
| `client.heartrate` | `client.py:1685` | `ClientHeartbeat` `timedelta(seconds=v)` `client.py:1050` | yes |
| `relay.bundlewait` | `relay/__init__.py:1144` | returner `>=`, `min(poll, bw - waited)` `relay/upstream.py:841-845`; site clients `-w str(v)` `cluster/launch.py:502` (client `parse_time`, ceil) | yes |
| `relay.poll` | `relay/__init__.py:1145` | site scheduler `poll_max` `server.py:169,306`; upstream `wait(min(poll, …))` `relay/upstream.py:541,584,829,845,858,890` | yes (default `0.1`) |
| `relay.retention` | `relay/__init__.py:1162` | `min(PRUNE_INTERVAL, v)`, `timedelta(seconds=v)` `relay/upstream.py:863-866` | yes |
| `relay.timeout` | `relay/__init__.py:1161` (`or None`) | site clients `-T str(v)` `cluster/launch.py:514-515`; client `-T` is `parse_time` (`client.py:1615`), `0` = never | yes, but rounded up by each client |
| `relay.upstream.heartrate` | `relay/__init__.py:1152` | `remaining = v - elapsed` `relay/upstream.py:1071` | yes |
| `relay.upstream.evict_hint` | `relay/__init__.py:1165` | `float(v)` `core/reconnect.py:280`; `:g` format `reconnect.py:556`, `relay/upstream.py:622` | yes |
| `relay.upstream.deadline` | `relay/__init__.py:1163` | `float(v)` `reconnect.py:279`; `wait(v)` `:527` | yes |
| `relay.upstream.backoff` | `relay/__init__.py:1164` | `float(v)` `reconnect.py:282` → `backoff_delay` cap | yes |

Import-time module defaults also read *user* config (raw text): `relay/__init__.py:98-116`
(`DEFAULT_RELAY_POLL`, `…_DEADLINE`, `…_BACKOFF`, `DEFAULT_EVICT_HINT`, `DEFAULT_SITE_EVICT`,
`DEFAULT_RELAY_BUNDLEWAIT`), `relay/upstream.py:90,105,108`, `relay/site.py:92`. `RelayApp.run`
passes every value explicitly, so these only matter to library callers. Built-in-default readers
(not user config): `server.DEFAULT_EVICT = default.server.evict` (`server.py:738`), so **`hs cluster`
never reads `server.evict`** (`cluster/__init__.py` has no `evict`; `cluster/remote.py:31-32,963`);
`client.DEFAULT_HEARTRATE = default.client.heartrate` (`client.py:1029`).

**Probes (today):**

| Case | Result |
|---|---|
| `HYPERSHELL_SERVER_EVICT=10m` / TOML `evict = "10m"` → `config.server.evict` | `'10m' str` (both) |
| `HYPERSHELL_SERVER_EVICT=600.5` / `=1e3` | `600.5 float` / `1000.0 float` (cmdkit coercion) |
| `hs server --no-db` with env `10m`, TOML `"10m"`, TOML `"600"` | exit 5, `TypeError: '>=' not supported between instances of 'str' and 'int'` + traceback file; no "listening" line (fails in `ServerThread.__init__`, `server.py:1212` → `:794`) |
| `hs server`, `HYPERSHELL_SERVER_EVICT=5` | exit 5, `RuntimeError: Evict period must be greater than 10 seconds: given 5` (check is `>= 10`) |
| `hs client`, `HYPERSHELL_CLIENT_HEARTRATE=10s` | exit 5, `TypeError: unsupported type for timedelta seconds component: str` + traceback |
| `hs relay`, `HYPERSHELL_SERVER_EVICT=10m` | exit 5, same `TypeError`, in `RelayThread.__init__` → `site_cluster` → `HeartMonitor` (after DB lock, `relay/__init__.py:1131-1133`) |
| `hs relay`, `…_RELAY_UPSTREAM_DEADLINE=10m` / `…_BACKOFF=1m`; TOML `evict_hint = "10m"` | exit 5, `ValueError: could not convert string to float` at `reconnect.py:279` via `relay/__init__.py:581` (after lock and `fence_site`) |
| `hs relay`, `RELAY_POLL=1m`, `RELAY_RETENTION=5m`, `RELAY_UPSTREAM_HEARTRATE=10s`, `RELAY_BUNDLEWAIT=30s`, TOML `timeout = "5MB"` | no error at start: runs until killed (readers are reached only after the upstream link is up, `relay/__init__.py:586-612`; `timeout` only when a client launches) |
| `HYPERSHELL_RELAY_UPSTREAM_EVICT_HINT=10m` | lands at `relay.upstream.evict.hint` (`{'hint': '10m'}`); `evict_hint` stays `600` (R1 defect) |
| `hs config get server.evict` / `which` (env `10m`) | `10m` / `10m (env: HYPERSHELL_SERVER_EVICT \| default: 600)` |
| `hs list --count` with env `SERVER_EVICT=10m` | exit 0 |

## 2. How the #106 duration keys convert

At the **reader**, never at load. Two paths:

- **Option-backed** (`server.poll`, `task.timeout`, `client.timeout`, `task.signalwait`,
  `submit.bundlewait`, `autoscale.period`): the key is the argparse default and argparse runs a *string*
  default through `type=parse_time` (#106 PLAN §Configuration, `PLAN.md:53-58`); a number skips
  `type=` and is used as given (#106 R5). Declarations: `server.py:1756-1786`, `client.py:1603-1620`,
  `cluster/__init__.py:153-263`, `submit.py:1355-1367`. Error → argparse, exit **2**:
  `argument -Q/--poll: Duration '5MB' is not valid: expected …` (probe, `HYPERSHELL_SERVER_POLL=5MB hs server`).
  `client.bundlewait` is not read from user config at all (`client.py:318` built-in; `-w` reads
  `submit.bundlewait`, `client.py:1603`).
- **Keys with no option**: `server.tls.handshake` via `handshake_seconds` (`core/tls.py:160-181`,
  `parse_time`, ceil, `0 < s <= 1 day`), called in `ServerApp.enable_tls` (`server.py:1939-1945`),
  `cluster/__init__.py:463`, `RelayApp.check_handshake` (`relay/__init__.py:1199-1205`), re-raised as
  `ArgumentError` → exit **2**. `autoscale.period` in `hs relay` via `RelayApp.site_period`
  (`relay/__init__.py:1207-1219`): `str` → `parse_time`, else pass through; `UnitError` →
  `ArgumentError(f'autoscale.period: {error}')`, exit **2**, raised from `check_args` before
  `bind_database`/lock.

**Established message** (probe): `<key>: Duration '<value>' is not valid: expected <TIME_FORMS>`, e.g.
`autoscale.period: Duration '5MB' is not valid: expected a number of seconds, units d, h, m and s in
that order, whole or fractional (e.g., 90, 45s, 90m, 1h30m, 1.5h, 1d12h), or a Slurm time MM:SS,
HH:MM:SS, D-HH, D-HH:MM or D-HH:MM:SS (e.g., 90:00, 1:30:00, 1-12)` (`core/types.py:135-137,155-160`).
It names the key but **not the source** (no variable/file). The blame-suffix pattern for key errors
is `ConfigurationError(f'… given {value!r} ({blame(config, *varpath)})')` → `(from: HYPERSHELL_X)` or
`(from: /path/config.toml)` (`cluster/remote.py:378-379`, `cluster/topology.py:543,551`,
`core/config.py:299-308`). `exit_status`: `bad_argument=2`, `bad_config=3`, `runtime_error=5`
(cmdkit probe). `ConfigurationError` raised in a command exits 3 without traceback via the shared
mapping (`core/exceptions.py:139`); at import it exits 3 for every command (`core/config.py:488-497`).

**Sibling compatibility (`cluster-nodb-and-zero-period`):** its R4/R5 read `autoscale.period` through
the argparse default and `RelayApp.site_period`, keep a number "as given", and pin `hs relay`'s exit
status to "the one it gives today" (2). So `autoscale.period` (and every option-backed key) must reach
the reader **as written** — text stays text so argparse applies `type=`; R5's env reader should give
these keys a "duration" type that takes a TOML number when the text is one, else keeps the text, and
never errors at load.

## 3. Rounding

`parse_time` returns `math.ceil` int seconds (`types.py:155-160`); `parse_delay` returns an
unrounded float (`:163-173`); both use exact `Fraction` from `_match_seconds` (`:147-152`). No R6
reader needs an int: every consumer above accepts a float (table §1). Only `relay.timeout` is
effectively whole, because its consumer is the site client's `-T`, which rounds up and treats `0` as
never.

Recommendation:
- `relay.timeout`: **round up** (`parse_time` semantics). Matches `client.timeout`, which it feeds;
  a fraction can never become `0` (= never); the launched word stays an integer
  (`launch.py:515 str(v)` would write `1e-05` for a tiny float, which every client refuses).
- The other nine: **keep exact** (GOAL clarification). Return `int` when the value is whole
  (`"10m"` → `600`), else `float`, so messages and `:g` read as today. Do *not* round
  `server.evict`: ceil would let `"9.5s"` pass the `>= 10` check while bare `9.5` fails.
- `relay.bundlewait` reaches site clients as `-w str(v)` (`launch.py:502`), rounded up there;
  writing it with `plain_seconds` (`launch.py:468-479`) avoids the exponent hazard
  (pre-existing for a TOML float).
- A huge digit string overflows `float(Fraction)`; catch `OverflowError` as `parse_delay` does
  (`types.py:168-172`).

## 4. Where to convert — recommendation: at the reader, through one helper, at start

One helper (e.g. `config_seconds(*varpath, whole=False)` in `core/config.py` or `core/types.py`):
number (not bool) → as given (#106 R5 rule); text → `_match_seconds`; refusal →
`ConfigurationError(f'{key}: Duration {value!r} is not valid: expected {TIME_FORMS} ({blame(config, *varpath)})')`
→ exit 3, no traceback. Called in each command's start check, before any bind, lock or launch:
`ServerApp.check_args` (`server.py:1884`; `run` calls it at `:1834` before `enable_tls`) for
`server.evict`; `ClientApp.check_args` (`client.py:1703`) for `client.heartrate`;
`RelayApp.check_args` (`relay/__init__.py:1176-1196`, beside `self.site_period`, before
`bind_database`/lock at `:1131-1133`) for `server.evict` and the eight `relay.*` keys, with
`RelayApp.run` (`:1142-1167`) passing the converted values. Point the import-time relay defaults
(§1) at `default.` as #106 did for `DEFAULT_SITE_PERIOD` (`PLAN.md:68-70`), so no module holds
user text.

Why not at load (a typed reader rewriting `"10m"` → `600` in the merged config):
1. **Blast radius.** Load runs at import of `core/config.py`; a bad value exits 3 in *every*
   command (`core/config.py:488-497`), including `hs list` and the `hs config edit`/`set` a user
   needs to fix it. R6 says "the command that reads it"; #106 REVIEW verified `hs list --count` and
   `hs config get` still exit 0 with a bad duration key (`REVIEW.md:46-48`; reprobed here).
2. **Files.** R5's typed reader is about the environment layer; a file string `"10m"` would need a
   second, all-layer pass, and `hs config get/which` would then show per-layer values that differ
   from the merged one.
3. **Consistency with #106 and the sibling.** The option-backed keys must stay text to reach
   argparse; converting only the ten at load would give "duration key" two regimes.
4. **`hs config get server.evict`** keeps showing what was written (`10m`), as
   `autoscale.period` does today (probe: `5m`).

Cost: one call per key per command; exit 3 here vs exit 2 for `autoscale.period`/`handshake` (#106
used `ArgumentError`). R6 asks for "a configuration error", so 3 is defensible; leave the #106 keys
at 2 (the sibling pins it). Optional: move the `server.evict < 10` refusal (`server.py:797-798`,
`RuntimeError`, exit 5) into the same check.

## 5. Docs (`docs/_include/config_param_ref.rst`)

Entries: `server.evict` `:293-306`; `client.heartrate` `:407-410`; `relay.bundlewait` `:734-737`;
`relay.poll` `:739-742`; `relay.retention` `:744-746`; `relay.timeout` `:835-836`;
`relay.upstream.heartrate` `:868-869`, `.deadline` `:871-877`, `.backoff` `:879-884`,
`.evict_hint` `:886-893`. None states a form beyond "seconds".

#106 wording to reuse (option-backed keys, e.g. `submit.bundlewait` `:470-473`, `task.signalwait`
`:519-521`, `server.poll` `:282-284`):

> A string takes the same forms as ``-w``/``--bundlewait``: units ``d``, ``h``, ``m`` and ``s``
> (e.g., ``"30s"``, ``"5m"``, ``"1h30m"``) or a Slurm time (e.g., ``"1:30"``); a number is seconds.
>
> .. versionchanged:: 2.9.0 — A string with units or a Slurm time.

`server.tls.handshake` (`:371-373`), the closest no-option precedent: "Takes a number of seconds or
any duration form (e.g., ``30``, ``"30s"``, ``"1m"``), rounded up to whole seconds; … or a TLS
server refuses to start." `autoscale.period` adds "``hs relay`` reads it in the same forms … and
stops before it starts if the value is not a duration" (`:595-597`). R6 keys have no option to point
at, so R9 should adapt the handshake sentence ("a number of seconds, or a string in the duration
forms: …; fractions are kept" / "rounded up" for `relay.timeout`). Fix `evict_hint`'s env reach in
the same pass (R1).
