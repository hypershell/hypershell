# 02 — How the four keys reach their commands, and where `hs relay` converts `autoscale.period`

GOAL-deferred question: "Where `hs relay` converts `autoscale.period`, and whether a string reaches
`hs cluster`'s or `hs server`'s autoscaler by any path other than the option." Researched
sequentially by the planner; probes on develop, 2026-11-14, in a `temp_site.sh` site.

## Probe: what the configuration hands over

`config` (cmdkit) coerces environment values: `HYPERSHELL_SERVER_POLL=60` → `int` 60,
`HYPERSHELL_TASK_SIGNALWAIT=0.5` → `float` 0.5, `HYPERSHELL_AUTOSCALE_PERIOD=90` → `int` 90, while
`HYPERSHELL_AUTOSCALE_PERIOD=5m` stays the `str` `'5m'`. A TOML `period = "5m"` is `'5m'`; a TOML
`bundlewait = 30.5` is `float` 30.5.

argparse runs `type=` over a default only when the default is a `str`. So on develop:

```
HYPERSHELL_AUTOSCALE_PERIOD=5m  → hs cluster / hs server: argument -P/--period: invalid int value: '5m'
[autoscale] period = "5m"       → same
relay.site.DEFAULT_SITE_PERIOD  → '5m'  (a str, read at import)
```

and `timedelta(seconds='5m')` raises `TypeError` (`cluster/remote.py:430`, the autoscaler's
`wait_check`), so the relay's site fleet would fail at start.

## Every read of the four keys (`grep` of `src/hypershell`)

| Key | Read through an option default | Read directly |
|---|---|---|
| `submit.bundlewait` | `submit.py:1279`, `server.py:1664`, `client.py:1603`, `cluster/__init__.py:150` | none (`DEFAULT_BUNDLEWAIT` in `submit.py:466` reads the built-in `default`, not `config`) |
| `task.signalwait` | `client.py:1619`, `cluster/__init__.py:245` | none (`client.DEFAULT_SIGNALWAIT` reads `default`) |
| `server.poll` | `server.py:1693`, `cluster/__init__.py:173` | none (`server.DEFAULT_SERVER_POLL` reads `default`) |
| `autoscale.period` | `server.py:1687`, `cluster/__init__.py:250` | **`relay/site.py:85` `DEFAULT_SITE_PERIOD`** |

The autoscalers of `hs cluster` and `hs server` get their period only from the option
(`cluster/__init__.py:324`, `server.py:1760`); their library defaults are the built-in
`default.autoscale.period` (`cluster/remote.py:328`) or a literal 60 (`cluster/topology.py:350,597`).
**No string reaches them by another path.** So swapping `type=int` for `parse_time` covers R3 for
every command except `hs relay`, as a7's decision 1 found for the timeouts.

## `hs relay`

`DEFAULT_SITE_PERIOD = config.autoscale.period` is evaluated when `relay/site.py` is imported, and
`hypershell/__init__.py:33` imports `RelayApp` for **every** `hs` command. It is the default of
`RelayThread(period=…)` (`relay/__init__.py:506`), which passes it to `site_cluster` and every
`zone_fleet`. `RelayApp.run` (`relay/__init__.py:1133`) is the only constructor of `RelayThread` in
`src/` and does not pass `period`.

Converting at import (`parse_time(config.autoscale.period)` in `site.py`) would make a bad value a
traceback in every command, `hs list` included. **Recommended:**

- `RelayApp` gains a cached property (e.g. `site_period`) that reads `config.autoscale.period`: a
  `str` goes through `parse_time`, a number passes unchanged (the same rule argparse applies to a
  default, so `hs relay`, `hs cluster` and `hs server` read every value alike, and R5's "a number in a
  configuration file keeps today's meaning" holds — a TOML `0.5` stays 0.5 rather than rounding up).
  A `UnitError` becomes `ArgumentError(f'autoscale.period: {error}')` — the `-W` message, naming the
  key, exit 2 (`bad_argument`) — raised from `check_args`, before the relay locks its database.
- `RelayApp.run` passes `period=self.site_period` to `RelayThread`.
- `DEFAULT_SITE_PERIOD` reads the built-in `default.autoscale.period` (int 60), like
  `cluster/remote.py:328`'s `DEFAULT_AUTOSCALE_PERIOD`, so importing the module never parses user
  configuration. It stays in `__all__`; no test or doc references it (grep of `tests/`, `docs/`).

Existing relay tests set `HYPERSHELL_AUTOSCALE_PERIOD='1'` in a subprocess (`test_relay_topology.py:1016`,
`test_relay_retire.py:403`); cmdkit coerces it to `int` 1, which passes unchanged — unaffected.

## High-blast-radius contact

None in this part: `relay/__init__.py` and `relay/site.py` are not on the list (§8's relay signal
carve-out and §10's relay database rebinding are not touched; `check_args` already runs before
`bind_database`).
