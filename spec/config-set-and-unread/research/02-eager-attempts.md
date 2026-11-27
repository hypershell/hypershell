# Research 02 — `server.eager` (R5) and `server.attempts` under `hs cluster` (R6)

Probed on `d9901cf0` (Python 3.14, throwaway site, a child interpreter per probe, nothing started
but one `hs cluster --no-db` run).

## Q1 — `--eager` must not replace the key

**Cause, confirmed.** cmdkit's `Application.from_cmdline` is `cls(**vars(interface.parse_args(argv)))`
and `__init__` `setattr`s every parsed name (`cmdkit/app.py:114-122`); `Interface` is a plain
`ArgumentParser` subclass (`cmdkit/cli.py:50`). `store_true` defaults to `False`, so the class
attribute `eager_mode = config.server.eager` (`server.py:1769`, `cluster/__init__.py:162`) is always
overwritten. `from_cmdline` runs no `check_args`, so it is a clean probe point.

Probe (file `eager = true` + `attempts = 3`, and `HYPERSHELL_SERVER_EAGER=true` +
`HYPERSHELL_SERVER_ATTEMPTS=3`), `(eager_mode, max_retries)`:

| argv | server today | cluster today | both, with the fix |
|---|---|---|---|
| none | (False, 2) | (False, 0) | (True, 2) |
| `--eager` | (True, 2) | (True, 0) | (True, 2) |
| `-r 5` | (False, 5) | (False, 5) | (True, 5) |

Unset: (False, 0) / (True, 0) / (False, 5) for both apps, today and with the fix. The fix was
probed by setting the parsed action's `default` to `config.server.eager`, which is exactly what the
keyword does.

**Recommendation:** add one keyword per app:
`interface.add_argument('--eager', action='store_true', dest='eager_mode', default=eager_mode)`.
`store_true` accepts `default=`. `store_const` would do the same with more change. `--eager` still
forces True, and `HYPERSHELL_SERVER_EAGER=false` overrides a file `true` (probed).

**Type of the value: a foreman decision.** #113 types only the environment layer. A file value is
passed through raw. With the fix, the probe gives `eager_mode` = `'no'`, `'false'`, `'yes'`, `0`, `1`
for those file values, and the readers use truthiness (`Task.next` `if eager:`,
`model.py:836`; `count_retryable` `not eager`, `model.py:815`). So `eager = "no"` and
`eager = "false"` would turn eager **on**. Today's `hs config set server.eager no` writes the text
`"no"` (GOAL Problem), and change 1 says files are not rewritten. So a 2.8 user who ran exactly that
gets the opposite of what they asked for after upgrade, which is the defect the GOAL names. Options:

- **A. Truthiness, as today** (zero code). Silently flips old `"no"`/`"off"`/`"false"` files to eager.
- **B. Refuse any non-bool, exit 3,** as `configured_failed_launches` does (`cluster/remote.py:362-368`:
  `ConfigurationError` naming the key, the value and `blame(...)`). This adds a start refusal for
  `"no"` files, a user-visible change not in the release note.
- **C. Read text by the environment's rule, refuse the rest, exit 3.** *Recommended.*
  `read_value(Kind.BOOL, str(value))` accepts `BOOL_WORDS` (`config_schema.py:176-179`), and a
  `ValueError` becomes `ConfigurationError` naming `server.eager`, `FORMS[Kind.BOOL]`, the value and
  `blame(config, 'server', 'eager')`. With C, a `"no"` file behaves as today (off), and only
  unreadable text (`"maybe"`, `2.5`) is refused.

  Put it in a small helper beside `DEFAULT_EAGER_MODE` in `server.py`, called from
  `ServerApp.check_args` (`server.py:1890`) and `ClusterApp.check_arguments` (`cluster/__init__.py:373`,
  which runs from `__enter__` before any bind, `:601`). Never call it at import: a raise there would
  stop `hs config set`, the command that fixes the file. `--eager` makes the value True, so it passes.

Under B or C the release note needs one line under change 4, naming the refusal for unreadable values.

## Q2 — R6: `-r` default under `hs cluster`

**Recommendation:** `max_retries: int = config.server.attempts - 1` at `cluster/__init__.py:163`, as
`server.py:1770` does. The probe gives cluster `max_retries == 2` with `attempts = 3`, and `-r 5`
still wins.

**Help: no change.** Both apps print the built-in value (`(default: {DEFAULT_ATTEMPTS - 1})`,
`server.py:1736`, `cluster/__init__.py:89`; `docs/_include/server_help.rst:64` and
`cluster_help.rst:129` say `(default: 0)`). That is the repo's convention: `-b` shows
`DEFAULT_BUNDLESIZE` though it reads `config.server.bundlesize`, and `hs server` already reads the key
under the same line. The R9 work is in `config_param_ref.rst:265-282`: `.attempts` and `.eager` name
no command, so they need `hs server`/`hs cluster` named, plus `versionchanged:: 2.9.0`.

Out of R6's scope, reported:

- **Library defaults disagree.** `LocalCluster`, `RemoteCluster`, `SSHCluster` and
  `AutoScalingCluster` default `max_retries=DEFAULT_ATTEMPTS` (=1, i.e. two attempts):
  `local.py:200`, `remote.py:261,963`, `ssh.py:250`. `ServerThread`, `serve_*` and the topology
  default to `DEFAULT_ATTEMPTS - 1` (`server.py:1174,1386,1500,1604`, `topology.py:590`). `ClusterApp`
  always passes the value explicitly, so this does not affect R6. Tests already pass `max_retries=0`
  to work around it (`test_stream_input.py:303`). Candidate seed.
- **A text file value crashes at import.** `attempts = "3"` in a file makes every `hs` command,
  `hs config set` included, crash with a `TypeError` traceback at import (`server.py:1770`, through
  `hypershell/__init__.py:27`). The env layer refuses it with exit 3.
- **Odd numbers pass through unchecked.** `attempts = 0`, `-1` or `2.5` give `max_retries` -1, -2 or
  1.5. R6 carries this into `hs cluster`. The effect is harmless (no retry, or one retry), because
  `attempt < attempts` filters (`model.py:795`). Candidate seed. No new check, per the non-goals.
- **New warning under R6.** `hs cluster --no-db` (or with no database configured) and `attempts > 1`
  now logs "Retries disabled when database disabled" (`server.py:1189-1190`), as `hs server` already
  does. This is consistent with change 5's "with a database".

## Q3 — Is every mode covered by the attributes?

**Yes, with no further edits.** `ClusterApp.run` passes `max_retries=self.max_retries` and
`eager=self.eager_mode` once to every launcher (`cluster/__init__.py:284-287`). From there:

- local: `LocalCluster` → `ServerThread` (`local.py:231-232`)
- `--launcher` / `--mpi`: `RemoteCluster` (`remote.py:291-292`)
- `--ssh`: `SSHCluster` (`ssh.py:282-283`)
- `--autoscaling`: `AutoScalingCluster` → `ServerThread` (`remote.py:1002-1003`) and `AutoScalerThread`
  (`attempts=max_retries + 1, eager=eager`, `remote.py:1037-1038`)

`ServerThread` builds `SchedulerThread(attempts=max_retries + 1, eager=eager)` (`server.py:1207`).
That keeps invariant §3 (`attempts == max_retries + 1`) with no new arithmetic. `watch.scheduled_work`
reads `machine.attempts`/`machine.eager` (`watch.py:151`). `hs server -A` →
`serve_with_sites` → `TopologyPublisherThread(attempts=max_retries + 1, eager=eager)`
(`topology.py:605,614`) also comes from `ServerApp`'s attributes. Under §4, in_memory has no
scheduler, so eager is inert there.

## Q4 — `hs relay`

**It reads neither key.** Its site server is `AutoScalingCluster(max_retries=0)` with the default
`eager=False` (`relay/site.py:136`). `relay/site.py:399-400` copies `machine.attempts`/`eager` from
its own scheduler, not from the configuration. The only readers are `server.py:1769-1770` and
`cluster/__init__.py:162`. No change, per the non-goal.

## Q5 — Tests

**Reuse `config_repr`** (`tests/test_env_config.py:84`). It runs a child interpreter in the site with
variables set, `imports=` lets it import the apps, and it uses a 60 s timeout. Use
`HYPERSHELL_CONFIG_FILE=<site>/config.toml` for the file layer. One child can read all six
`(app, argv)` pairs:

```python
'[(a.from_cmdline(argv).eager_mode, a.from_cmdline(argv).max_retries) '
'for a in (ServerApp, ClusterApp) for argv in ([], ["--eager"], ["-r", "5"])]'
```

Nothing new is imported at module level. A draft of tests 1-3 ran on `d9901cf0` (outside the repo):
2 failed by assertion and 2 passed, in 1.8 s.

**Marker:** precedent marks child-interpreter probes `@mark.integration` (all 31 in
`test_env_config.py`, and `read_defaults` in `test_duration_options.py:283`). `@mark.unit` works too.
The foreman picks; tag every test either way.

Proposed file: `tests/test_config_unread.py`. The R7 and R8 briefs may share it.

| # | node id | on d9901cf0 |
|---|---|---|
| 1 | `test_eager_attempts_read[file]`, `[env]`: `eager=true`, `attempts=3` → `[(True,2),(True,2),(True,5)]` for both apps | **red** (server `(False,2)`, cluster `(False,0)`) |
| 2 | `test_unset_as_today`: `[(False,0),(True,0),(False,5)]` for both | guard |
| 3 | `test_eager_env_false_over_file`: file `true`, `HYPERSHELL_SERVER_EAGER=false` → as #2 | guard |
| 4 | `test_attempts_alone_cluster`: `attempts=3` only → `ClusterApp.from_cmdline([]).max_retries == 2`, `eager_mode is False` | **red** (0) |
| 5 | *(C only)* `test_eager_text_word[no\|off\|false\|0]`: a file text value; child runs `ClusterApp.from_cmdline([f,'--no-db'])` then `check_arguments()`, and reads `eager_mode` → `False`. Same for `ServerApp` + `check_args()`, with `HYPERSHELL_SERVER_AUTH` (16+ chars) set. | guard (catches option A) |
| 6 | *(C only)* `test_eager_text_word[yes]` → `True` after the check | **red** (False) |
| 7 | *(B or C)* `test_eager_unreadable_refused[cluster\|server]`: file `eager = "maybe"`; `hs cluster t.in --no-db` / `hs server t.in --no-db` via `run_bounded` → `exit_status.bad_config`, stderr names `server.eager` and the file | **red** (cluster probed: runs, exit 0) |

**End-to-end: not needed.** No test exercises `--eager` ordering end to end today (no `--eager` in
`tests/`). The config value reaches the identical code path `--eager` takes, the moment it lands in
`eager_mode` (Q3). An ordering test would depend on timing (bundlesize 1, one thread) for no extra
coverage. If the reviewer wants a pass-through check, reuse `built_clusters`/`run_mode` from
`test_cluster_advertise.py:136-169` and assert `AutoScalingCluster.autoscaler`'s
`attempts == 3`/`eager`. Those run in_memory for the other modes, so they have no scheduler to read.

**Doc nit (R9 territory):** the `--eager` help says "If `--max-retries` is greater than one"
(`cluster_help.rst:139`, `server_help.rst:74`). Measured on `-r`, it should be "greater than zero";
the param reference's `.attempts > 1` is right.
