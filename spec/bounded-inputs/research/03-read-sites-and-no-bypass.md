# 03 — Numeric read sites, and making R6's "no bypass" checkable

Research for `hs-plan`, cycle `bounded-inputs`, on `fix/bounded-inputs`. Paths are under `src/hypershell/`.
Counts are from an AST scan of the source plus a grep for the dynamic forms.

## 1. Where each numeric key is read

About **70 config read sites** cover the 50 keys, in 12 files: 57 attribute chains on `config`, `_config` or `base`,
4 `config_seconds` calls, 3 handshake `.get`s, and about 6 dynamic reads. Another 35 reads take built-ins from
`default.*` to make `DEFAULT_*` constants.

"Option default" means the value becomes the default of an argparse option. **argparse runs `type=` on a default
only when it is a `str`** (checked with `cmdkit.cli.Interface`). So a TOML number never reaches the option's parser,
and a text value that fails it exits 2 with the option's name, not the key's.

| Key | Read sites | Use | Parsed today |
| --- | --- | --- | --- |
| `server.port` | `client.py:1661`, `cluster/__init__.py:224`, `server.py:1874`, `submit.py:1617`; `core/queue.py:1370` | option default `type=int`; `QueueConfig.load()` as given | text only |
| `server.queuesize` | `server.py:1283` (`ServerThread`), `core/queue.py:1372` | `QueueConfig(size=…)` | no |
| `server.bundlesize` | `server.py:1836`, `cluster/__init__.py:157` | `-b type=int` | text only |
| `server.bundlemax` | `core/queue.py:1528` (`QueueServer.__init__`, the relay-local server too) | `Dispatcher(bundlemax=…)` | no |
| `server.attempts` | `server.py:119` `configured_retries()` → `attempts - 1`, called at import (`server.py:1850`, `cluster/__init__.py:170`) and per call (`server.py:1274`, `cluster/remote.py:998`, `cluster/topology.py:601`) | arithmetic | **no** (text crashes at import) |
| `server.poll` | `server.py:1868`, `cluster/__init__.py:183` | `-Q parse_time` | text only |
| `server.evict` | `server.py:1994`, `cluster/__init__.py:391`, `relay/__init__.py:1335` | `config_seconds` (+ `check_evict`, but not in the relay) | form yes, range partly |
| `server.tls.handshake` | `server.py:2051`, `cluster/__init__.py:489`, `relay/__init__.py:1271`; `core/tls.py:510`, `core/queue.py:855` | `handshake_seconds` (own range 1…86,400) | yes |
| `client.bundlesize` | `client.py:368` `advertised_size` | `read_value(Kind.INT)`, exit 3 | form only |
| `client.bundlewait` | `client.py:1673` | `-w parse_time` | text only |
| `client.heartrate` | `client.py:1790` | `config_seconds` | form only |
| `client.timeout`, `.cores`, `.memory`, `.ratelimit` | `client.py:1683-1706`; `cluster/__init__.py:242-254` | `or None` → `-T parse_time`, `-C int`, `-M parse_bytes`, `-R parse_rate` | text only; cluster adds a float rate check (`:393-400`) |
| `submit.bundlesize` / `.bundlewait` | `submit.py:1585/1588`; `.bundlewait` also at `server.py:1839`, `cluster/__init__.py:160` | `-b int` / `-w parse_time` | text only |
| `task.cores`, `.memory`, `.timeout` | `submit.py:1594-1599`, `server.py:1842-1844`, `cluster/__init__.py:237-253`, `client.py:1682` | `or None` → int / `parse_bytes` / `parse_time` | text only |
| `task.signalwait` | `client.py:1689`, `cluster/__init__.py:258` | `-S parse_time` | text only |
| `autoscale.factor` | `server.py:1861`, `cluster/__init__.py:262` | `-F type=float` | text only (`nan` accepted) |
| `autoscale.period` | `server.py:1862`, `cluster/__init__.py:263`, then `parse_period` again in `check_args` (`server.py:1975`, `cluster/__init__.py:388`); `relay/__init__.py:1324` | `parse_period` | yes |
| `autoscale.size.min/max/init` | `cluster/__init__.py:264-266` | `-X/-Y/-I int` | text only |
| `autoscale.failed_launches` | `cluster/remote.py:374` | `configured_failed_launches` | yes |
| `logging.file.keep` | `core/logging.py:1047` (`.pop` on a copy) | `set_retention_count` (`int()`, ≥ 0) | partly |
| `database.port` | `data/core.py:57` (`.pop` on a copy) | URL | no |
| `relay.port` | `core/config.py:667` (constant at import), `relay/__init__.py:1153` | `--port int` | text only |
| `relay.bundlesize` | `relay/__init__.py:115` (constant), `:1206` | `RelayThread` | no |
| `relay.size` | `relay/__init__.py:121` → `-N int`; `check_args` `:1252` | ≥ 1, exit 2 | partly |
| `relay.min/max/init` | `relay/__init__.py:1343` `config.relay.get(key)` (dynamic key) | `relay_bounds`, ≥ 0, exit 2 | partly; the `_env`/`_eval` forms are never read |
| zone `size/min/max/init` | `relay/__init__.py:1371` `.get('zone')` → `zone_settings` `:1388-1400`, `zone_bounds` `:283-296` | size ≥ 1, bounds ≥ 0, exit 2 | partly |
| `relay.num_threads` | `relay/__init__.py:1222` → `site_launch` → `cluster/launch.py:522` `str()` as `-N` | the launched site client's own `-N` | **only in each child, mid-run** |
| `relay.bundlewait/poll/retention/timeout`, `relay.upstream.heartrate/deadline/backoff/evict_hint` | `relay/__init__.py:1335` (`RELAY_DURATIONS`, `:130`) | `config_seconds` | form only |
| `relay.upstream.port` | `relay/__init__.py:1019` (constant) → `-p int`; `relay/upstream.py:166` `int(port or section.port)` | `int()` | text only |
| `relay.upstream.bundlesize` | `relay/upstream.py:87` (constant), `relay/__init__.py:1214` | `RelayThread` | no |

Reads after start-up: the relay (`durations`, `site_period`, `zone_settings`, `upstream_config`), the
relay-local `QueueServer` (`server.bundlemax`, `server.queuesize`), and `configured_retries()` in server and
cluster constructors. The autoscaler reads no key itself: `failed_launches` and `period` come in as arguments.
Launched clients, and the relay after it re-executes (`bind_database`, `relay/__init__.py:1422`), import again and
load again. Nothing calls `reload` after start-up.

## 2. What a check at load alone would miss

1. **`_env` and `_eval` forms.** cmdkit's `Namespace.__getattr__` (`cmdkit/namespace.py:80-103`) resolves the form
   on every attribute access and caches nothing, so an `_eval` command runs again at every read. A value checked
   at load is therefore not the value read later, unless the load check pins it. Readers that use `.get` or `[]`
   (relay bounds, zone tables, `logging.file`, `database`) never see these forms.
2. **Option defaults.** Numbers skip `type=`, and refused text exits 2. So the check has to run before the
   command classes are built, and it has to leave native numbers behind.
3. **Import order.** `hypershell/__init__.py:26-33` imports every command module, and their class bodies read
   config at import: `configured_retries()` at `server.py:1850` is how `attempts = "3"` crashes every command.
   The only point earlier than that is the load in `core/config.py:641-655`. Two consequences follow:
   - `hs config` can be exempt from refusals of a file value (C2) only by reading the command line, as
     `_relay_requested` does (`core/config.py:686-692`).
   - `import hypershell` exits 3 on a bad file, as it already does for any `ConfigurationError`.
4. **In-place changes in tests.** Five test files set config values in place after load (`autoscale.period`,
   `server.attempts`, `server.tls.handshake`). Tests that rely on today's checks at the read site will have to move
   to the load path.
5. **The `base=` parameters** of `config_seconds`, `advertised_size` and `client_host` take a caller's
   `Configuration`, which the load check never sees.
6. **An existing gap:** the relay converts `server.evict` without `check_evict`. Only `HeartMonitor.__init__`
   (`server.py:889`) refuses it, when the relay-local server starts, after the database lock is taken, with exit 5.

**R10: Python API defaults that come from configuration.**

- `serve_from` (`server.py:1449`), `serve_file` (`:1565`), `serve_forever` (`:1675`) and `ServerThread`: `max_retries=None`
  becomes `configured_retries()`. `ServerThread` always reads `server.queuesize` (`:1283`), and `QueueServer` always reads
  `server.bundlemax` (`core/queue.py:1528`), with no parameter for either.
- `run_local`, `run_cluster`, `run_ssh`, `AutoScalingCluster` (`cluster/remote.py:998`) and `serve_with_sites`
  (`cluster/topology.py:601`): the same `configured_retries()`, and the `ServerThread` reads above.
- `RelayThread` (`relay/__init__.py:525-535`): `DEFAULT_RELAY_PORT`, `DEFAULT_RELAY_BUNDLESIZE`, `DEFAULT_RELAY_SIZE` and
  `DEFAULT_UPSTREAM_BUNDLESIZE` are fixed from `config` at import. `upstream_config()` reads `relay.upstream.port` when called.
- `QueueConfig.load()` and `QueueInterface.new()` (`core/queue.py:1361-1373`, `:1410`): `server.port` and `server.queuesize`.
- `run_client`, `ClientThread`, `submit_from` and `submit_file` take built-in `default.*` values only.

An import-time check that pins values covers all of these with no change to their signatures.

## 3. Making "no bypass" checkable

- **(a) An AST or grep allow-list.** A test fails on any `config.<section>.<numeric>` or `config_seconds(...)` outside
  allow-listed modules. That is about 70 sites to route, so in practice it becomes (b). It also misses
  dynamic reads (`config.relay.get(key)`, `RELAY_DURATIONS`, `section = config.relay.upstream`,
  `.pop` on the logging and database copies) unless it also flags `.get` or `.pop` of every numeric leaf name and follows
  aliases, which an AST test cannot do in general. It proves the code's shape, not its values.
- **(b) One accessor, `setting('server.evict')`.** About 70 edits in 12 files, the class bodies included (it must work
  at import), with (a) as its gate. It fixes item 1 of section 2 (resolve once, cache, know the source). It still
  needs (c), because C2 checks keys that no command reads (a bad `relay.retention` stops `hs list`). It is the
  largest diff and has the most test churn.
- **(c) Check at load, and test the table.** One test fails when a numeric `KEYS` or `ZONE_KEYS` entry has no
  range, or when a default lies outside its range. A parametrized test has load refuse each key one past each
  bound, from a file and from a variable, with exit 3. This proves R5, but not R6 on its own, because of items 1–2
  of section 2.

**Recommendation: (c), made structural by pinning, plus two cheap gates.**

1. The load check resolves each numeric key once, `_env` and `_eval` forms included, parses it by its kind and
   range-checks it.
2. It writes the result back into the layer that gave it: text becomes a number for `INT`, `NUMBER` and
   `INT_OR_NONE`, and a resolved variant becomes the plain key. `which()` and `blame` therefore still name the
   file or the variable.
3. It leaves checked duration and size text as text. The readers round differently today: `parse_time` rounds up,
   `config_seconds` keeps fractions, `relay.timeout` is whole, and `parse_period` makes the least value 1. Turning
   `"1.5s"` into the number 1.5 would skip `-w`'s rounding up, which R2 forbids.

After this, every read site sees a checked value, wherever it reads from, and none of the ~70 sites has to change.

- **Gate 1, a runtime invariant test.** After load from fixtures holding every form (TOML number, text, `_env`,
  `_eval`, a zone table), every numeric key is a native number or checked text, and no numeric `_env` or `_eval`
  form is left in the merged view.
- **Gate 2, an AST test.** Every `add_argument(..., type=X)` of a numeric option names a parser from the table, with
  no bare `int` or `float` (about 45 options). A grep also checks that the retired range checks are gone
  (`EVICT_MINIMUM`, `RATE_MINIMUM` comparisons, `MAX_HANDSHAKE`, `GROUP_MIN` outside the table).

**Cost:**

- About 150 lines for the checker and the pinning.
- About 12 ad-hoc checks deleted or reduced to relations, plus the three tests above.
- The five test files from item 4 of section 2.
- One behaviour change to note: an `_eval` command for a numeric key runs once at load, not at every read.

## 4. Ad-hoc checks that become table entries

| Check | Message today | Exit | Callers |
| --- | --- | --- | --- |
| `parse_period` (`core/types.py:167-179`) | `Period '0' is not valid: must be greater than zero`, prefixed `autoscale.period: ` for config | 2 | `-P` (`server.py:1866`, `cluster/__init__.py:270`); `server.py:1975`, `cluster/__init__.py:388`, `relay/__init__.py:1324` |
| `parse_rate` and `RATE_MINIMUM` (`core/types.py:182-203`) | `Rate 'x' is not valid: expected a number` or `… expected 0 (no limit) or at least 0.000001 tasks per second in size` | 2 | `-R` (`client.py:1687`, `cluster/__init__.py:248`). It takes `abs()`, so a negative rate is accepted today. |
| cluster rate checks (`cluster/__init__.py:393-400`) | `client.ratelimit: Rate … expected a finite number (from: …)` | 2 | `check_arguments` only. A float only (an `int` slips past), and `hs client` has none. |
| `check_evict` (`server.py:826-834`) | `Evict period must be greater than 10 seconds: given X` (it tests `>=`), `… a finite number …`, `… at most 86399999913600 seconds …`. `{seconds}` raises for a 4,300-digit integer. | 5 (`RuntimeError`) | `server.py:1994`, `cluster/__init__.py:391`, `HeartMonitor` `server.py:889` (an API path, which stays per C3) |
| `configured_failed_launches` (`cluster/remote.py:372-378`) | ``Expected a non-negative integer for `autoscale.failed_launches`, given X (from: …)`` | 3 | `cluster/__init__.py:348-350`, forced at `:455` |
| `check_arguments` period and rate (`cluster/__init__.py:384-400`) | above | 2 (moves to 3, C1) | `ClusterApp.run` |
| `GROUP_MIN`, `GROUP_MAX`, `ensure_group`, `parse_group` (`data/model.py:64-115`) | `task group must be an integer from -2147483648 to 2147483647, not "X" (origin)` | input: 5 (`TaskGroupError`); `-g`: 2 | `submit.py:1459, 1794`, `data/model.py:640, 732`; `group_option` `submit.py:1464` → `-g` `:1603`. `hs list` and `hs update` `-g` and `--part` are plain `int` (`task.py:675, 678, 1039`). |
| `config_seconds` (`core/config.py:345-375`) | `KEY: Duration 'x' is not valid: expected <forms> (from: …)`; `… could not be resolved: <Type>`; `KEY_env names V, which is not set` | 3 | `client.py:1790`, `server.py:1994`, `cluster/__init__.py:391`, `relay/__init__.py:1335`. It has no range, so a negative or huge number is returned as given. |

Further ad-hoc checks, not in the GOAL's list, that R6 also touches:

- `handshake_seconds` (`core/tls.py:160-179`): range 1…86,400 s, exit 2. This **conflicts with the table's Duration
  range of 0…2³¹−1 for `server.tls.handshake`**. The plan should either give the key its own entry, justified under
  R1 in TECH.md, or drop the one-day cap.
- `set_retention_count` (`core/logging.py:331-337`): `Invalid file retention count`, exit 3 through `panic`.
- The relay checks `size < 1` (`relay/__init__.py:1252`), `relay_bounds` (`:1339-1350`), `zone_settings`
  (`:1388-1400`) and `zone_bounds` (`:283-296`), all exit 2. Their range parts become entries; the relations
  `min ≤ init ≤ max` stay, as a non-goal.
- `advertised_size` (`client.py:351-378`): form only.
- `configured_retries` (`server.py:117-119`): no check at all.
