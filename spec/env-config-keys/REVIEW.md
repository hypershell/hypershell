# REVIEW — Environment variables reach every configuration key with the value as written; the remaining duration keys take units

> Adversarial QA by `hs-review`, run in an isolated/clean context. The correctness pass grades the
> branch diff against [`GOAL.md`](GOAL.md) + the AGENTS.md invariants **only** — it does not see
> `PLAN.md`/`TECH.md` (avoids grading-its-own-homework / plan-sycophancy). Every finding cites an
> **executed** command, not an assertion.

- **Reviewed commit:** f495fa20cb17ebecb32978a6c2288d75ce217786  ·  **Base:** `origin/develop` b17ff1ae (merge-base 90ab2fc2)  ·  **Date:** 2026-11-15
- **Verdict:** changes-requested
- **Cycle:** 1 of ≤3 — mirrors `review.cycle` in `TECH.md` (escalate to human on non-convergence)

The blind reviewer was given `GOAL.md` (with its GOAL-gate block), the spec-excluded diff, the base
copies of `review-rubric.md`, `invariants.md` and `AGENTS.md`, and the gate rulings as contract
readings (type per key; the untyped TOML rule; the boolean words; no `__` spelling; fractions kept,
only `relay.timeout` rounded; unmatched names held, the four process variables dropped; 2.9 only;
duration keys refused at start with exit 3, #106's keys exit 2; a typed variable that does not
convert stops every command at import; empty text refused on a boolean or number key and unset on
an optional text key; the key variable recognised by the matcher wherever it is handled by name;
`HYPERSHELL_EXPORT_foo` beside a file's `foo` gives two variables; two variables naming one key
refused; a file's `[export]` keeps its upper-casing). It did not read PLAN, TECH, research or META.
The contract-drift check found only the shaping commit (2e92b87c) and the GOAL-gate commit
(94319cdb) on `GOAL.md`, both before the build.

## Verification run

- Full suite, serial, at f495fa20 (`.venv/bin/python -m pytest -q -p no:cacheprovider`, sandbox off)
  → **3245 passed, 116 skipped** (23m58s); `test_waits_for_slow_peer_upgrade` passed in the run.
- New and touched test files (`test_config_schema`, `test_env_blame`, `test_env_config`,
  `test_env_duration`, `test_env_export`, `test_completions`, `test_cluster_launch`,
  `test_autoscale_failed_launches`) → 379 passed, 1 skipped; `test_auth_variants`,
  `test_relay_launch`, `test_logging` → 159 passed.
- The new tests against the base source (`git archive origin/develop`, `PYTHONPATH=<base>/src`) →
  76 failed, 23 passed; the 23 are must-keep guards (16 `test_kept[*]`, export environment over
  file, a file's table as before) and 5 static completion checks.
- Side-by-side loader probe, base vs branch: all 110 entries of the key table in upper and lower
  case, and all 68 `HYPERSHELL_*` names used in `src/`, `docs/`, `tests/`, `share/` and `.github/`
  → every variable that set a key on the base sets the same key on the branch; every documented key
  is reached; no two keys share a name.
- `sphinx-build -W --keep-going -E -b html docs …` (Python 3.12 docs environment) → exit 0, no
  warnings.
- Import cost (median of interleaved runs): `hs config get logging.level` 344.9 ms base vs 346.4 ms
  branch; with 400 extra `HYPERSHELL_*` variables, `import hypershell.core.config` 1267 ms base vs
  373 ms branch. No regression.
- CLI drives in `.agents/factory/bin/temp_site.sh` with an empty `HYPERSHELL_CONFIG_FILE`: `hs
  cluster --launcher=env` (F1), `hs list`/`hs config get` with empty variables (F2), a file
  `[database]` with an empty password (F3), `hs relay`/`hs server`/`hs client` with bad and unit
  durations (R6), `hs cluster` with `[export]` and `HYPERSHELL_EXPORT_*` (R7).
- Merge checks (`git merge-tree --write-tree`), see **Merge status** below.

## Requirement → evidence matrix

| R-ID | Implemented by | Verified how | Status |
|------|----------------|--------------|--------|
| R1 | `core/config_schema.py` (key table, matcher), `core/config.py` (`load_env`), `cluster/remote.py` (`failed_launches`) | Side-by-side probe of every key and every variable in use; `server.tls.min_version`, `relay.launcher_args` (leaves `relay.launcher` text), `autoscale.failed_launches`, `database.timescale.chunk_interval` land; `hs config get` shows them | ✅ |
| R2 | matcher open tables and zones | `database.pragmas.journal_mode`, `database.ssl_mode`, `ssh.nodelist.my_group`, zone `gpu_a100` `launcher_args`/`size`; `HYPERSHELL_RELAY_ZONE_GPU` and `RELAY_ZONE` + a zone table refused, naming the variables (exit 3) | ✅ |
| R3 | `_ENV`/`_EVAL` through the auth forms | `hs config get server.auth` via `_ENV` and `_EVAL`; `relay.upstream.auth_eval`; `database.password_env` gives the file form's URL; two forms in one layer refused; an unset `_ENV` target named | ❌ F1 |
| R4 | kind readers | Key `0012345678901234`: branch server accepts it and a client connects (base refuses it as too short); `test_all_digit_key_*` pass | ✅ |
| R5 | kind readers, import-time refusal | `HYPERSHELL_SERVER_PORT=abc` stops `hs config get`, `hs config which`, `hs list`, `hs --version` with exit 3 naming the variable and form; bad durations or sizes never stop import | ✅ (F2 on empty text) |
| R6 | `config_seconds`; `ServerApp`/`ClientApp`/`RelayApp.check_args` | Each of the nine relay keys given `5x` makes `hs relay` exit 3 before binding; `server.evict` (file, env) stops `hs server`; `client.heartrate` (env, `_EVAL`) stops `hs client`; messages name key, source and forms; #106's options still exit 2; `RelayApp.durations` gives `relay.timeout` 2 from `1.2s`, `relay.poll` 0.1, `relay.upstream.evict_hint` 5400 | ✅ |
| R7 | `load_task_env` | `hs cluster` end to end: base `RUN_ID=7 MyVar=[] MYVAR=x E=[null]`, branch `RUN_ID=007 MyVar=[x] E=[]`; a file's `foo = [1, 2]` gives `FOO=1:2` | ✅ |
| R8 | `env_variable`, `blame`, `hs config which`, `--list-available` | `which` names the variable as written; one key per line; no `site`, `config.file` or `server.tls.min.version`; bash and zsh completion consume the list | ✅ |
| R9 | `config_intro.rst`, `_alt`, `config_param_ref.rst`, `config_task_env*.rst`, `manual.rst` | Docs build clean under `-W`; claims checked against the code; `versionchanged:: 2.9.0` on changed entries | ✅ (F2(a) wording) |

Unmapped changes (possible scope creep): the file-layer half of F3. Mapped and sound: `-w`/`-T`
written with `plain_seconds` (identical words for whole numbers; a fractional or exponent value
reaches a client in a form its parser takes), the relay's `DEFAULT_*` duration constants reading
`default`, and the `hs config edit` paragraph moved into `config_intro.rst` (same page).

## Findings

### F1 [HIGH/CONFIRMED] Every launched client is refused when the environment gives the queue key in a form other than its canonical variable (R3)
- **Where:** `src/hypershell/cluster/launch.py:134` and `:243`; the refusal is the environment
  layer's two-forms check in `src/hypershell/core/config.py`.
- **Failure scenario:** the user gives the key by `HYPERSHELL_SERVER_AUTH_ENV` (or `_EVAL`, or the
  variable in another letter case) and runs `hs cluster` in launcher, `--mpi` or autoscaling mode.
  Each launched client refuses its configuration at import (exit 3, two forms of the key), and
  `hs cluster` ends with `ClusterLaunchError` (exit 5).
  A file's `auth_env` sits in another layer and never does this, so R3's "resolve as the file form
  does" is not met. Introduced: the base ran these launches.
- **Evidence:** `HYPERSHELL_SERVER_AUTH_ENV=MYKEY MYKEY=abcdefghijklmnop1234 temp_site.sh … hs
  cluster tasks.in --no-db --no-tls --launcher=env -N1` → base: tasks print `one`, `two`, exit 0;
  branch: `ClusterLaunchError: every launched client has exited with tasks remaining: … exited with
  status 3`, exit 5. The reviewer's run shows the client's message (`More than one form of
  server.auth (auth, auth_env) in one configuration layer (env)`) and the same outcome for the
  letter-case variant (`… both set server.auth`). Without the variable both exit 0.
- **Also to check in the fix (PLAUSIBLE, not run):** the `--ssh` launch and the relay's site
  launches, which were not driven with these forms.
- **Coverage:** no test launches a client with the key given in one of these forms.
- **Touches:** R3; the queue key's handling in launches (invariants §11).

### F3 [HIGH/CONFIRMED] An empty `database.user`/`password` from a file is now refused, with no prose or marker (§12; unmapped)
- **Where:** `src/hypershell/data/core.py:50-57` (`DatabaseURL.__init__`, `or None` on both fields).
- **Failure scenario:** a configuration file with `[database] provider = "postgres"`, `user = "u"`,
  `password = ""`. Base: accepted, and the URL silently drops the user
  (`postgresql://localhost/d`, connecting as the local account). Branch: every command, `hs config`
  included, exits 3 with `Must provide 'password' if 'user' provided`; `user = ""` with a password
  is refused the same way.
- **Evidence:** `DatabaseURL(provider='postgresql', database='d', host='localhost', user='u',
  password='')` → base `postgresql://localhost/d`; branch `REFUSED Must provide 'password' if 'user'
  provided`. A file drive with that table: base passes URL validation, branch stops at import.
- **Assessment:** for the environment form the `or None` keeps the base's outcome (an empty
  variable was `None` there) and is in scope. For a file it is a user-visible change no R-ID asks for,
  and `config_param_ref.rst`'s `.user`/`.password` entries carry no prose or `versionchanged`. Either
  confine the empty-as-unset reading to the environment layer, or keep it and document it (prose and
  `.. versionchanged:: 2.9.0` on both entries, and the release note). The refusal itself is a
  defensible reading of "if provided a password must also be provided".
- **Touches:** AGENTS.md §12 (same-commit docs rule); scope.

### F2 [MEDIUM/CONFIRMED] Empty text on an optional text key is not read as unset in two readers (ruling; R5)
- **Where:** `src/hypershell/core/config_schema.py` (`Kind.LOGGING_FILE` reader) with
  `src/hypershell/core/logging.py:1024-1027`; `src/hypershell/data/core.py:50-57` (only `user` and
  `password` are normalised).
- **Failure scenario (a):** `HYPERSHELL_LOGGING_FILE=` → every command writes a background-thread
  traceback, `IsADirectoryError: [Errno 21] Is a directory: '<cwd>'`, and still exits 0. Base: no
  file logging. The parameter reference ("any other text is a path") documents this outcome.
- **Failure scenario (b):** `HYPERSHELL_DATABASE_HOST=` (or `HYPERSHELL_DATABASE_DATABASE=`) with
  SQLite → every command exits 3, `Cannot provide 'host' for SQLite`. Base: works.
- **Evidence:** `HYPERSHELL_LOGGING_FILE= temp_site.sh sh -c 'hs list --count'` → branch stderr has
  one `Traceback` with `IsADirectoryError`, base none; `HYPERSHELL_DATABASE_HOST= … hs config get
  logging.level` → base exit 0, branch exit 3 with the message above.
- **Note:** a file's `file = ""` or `host = ""` behaves the same on the base, so the environment
  layer now matches a pre-existing file-form behaviour, against the ruling that empty text on an
  optional text key reads as unset. Related, not graded as a defect: `HYPERSHELL_SERVER_BIND=` makes
  `hs server` listen on all interfaces (base: a `TypeError`, exit 5), which is what a file's
  `bind = ""` does and what R4's "an empty value stays empty" gives; the fix should decide `bind`
  together with (a) and (b) and the guide should say what an empty bind does.
- **Coverage:** no test sets these keys empty.
- **Touches:** R5, R9 (the `logging.file` wording), the empty-text ruling.

### F4 [LOW/CONFIRMED] Two new seeds cite base line numbers this branch moves
- **Where:** `issues/config-keys-unread.md:29` cites `src/hypershell/cluster/remote.py:963` for
  `evict_after: int = DEFAULT_EVICT` (line 949 at HEAD; 963 is `task_memory=memory,`);
  `issues/config-set-coerces-text.md:29` cites `src/hypershell/core/config.py:394` for
  `resolve_auth`'s `str(value)` (line 457 at HEAD; 394 is blank).
- **Evidence:** `sed -n 963p src/hypershell/cluster/remote.py`, `grep -n 'return str(value)'
  src/hypershell/core/config.py` at f495fa20. Both seeds say they were probed on 90ab2fc2, where the
  numbers hold, but they land with this branch. Their other claims hold at HEAD (the
  `server.eager` claim was re-run: `eager_mode == False` with `HYPERSHELL_SERVER_EAGER=true` in both
  apps).

## Merge status

- **`origin/develop` (b17ff1ae, #111 on top of the merge base):** merges cleanly (`git merge-tree
  --write-tree origin/develop HEAD`, exit 0). On the merged tree, `test_update`, `test_list`,
  `test_contended_write` and the five new test files → 240 passed, 45 skipped.
- **`origin/fix/cluster-nodb-and-zero-period` (77a3af79, #112):** merges cleanly as text (exit 0;
  overlapping files `server.py`, `relay/__init__.py`, `config_param_ref.rst`, `ROADMAP.md`). The
  merged `ServerApp.check_args` and `RelayApp.check_args` read coherently. **Semantic conflict in
  #112's tests:** on the merged tree, 8 of `tests/test_period_floor.py::test_period_key_refused`
  fail — `env-nan` and `env-neginf` for `server-file`, `server-autoscaling`, `cluster-file` and
  `relay`. With this branch the environment's `nan`/`-inf` reach the period as text and are refused
  as not a duration (status 2, nothing served or launched), while the test expects the "greater
  than zero" message it gets from the base's coerced float. The other 862 tests in the overlapping
  set (`test_period_floor`, `test_cluster_no_db`, `test_duration_options`, the new env tests,
  `test_config_schema`, `test_relay_launch`, `test_autoscale_failed_launches`) pass. Whichever cycle
  lands second updates those eight expectations; the behaviour stays a refusal.

## Human-gate triggers

- **Triggered.** F1 concerns how the queue key reaches launched clients, a security invariant
  (invariants §11, the queue key's handling), and its fix changes that path. Human sign-off is
  required before the remediation is published.
- F2, F3 and F4 touch no high-blast-radius file and no security or DB-lifecycle invariant.

## Optional completeness sub-pass (separate reviewer; may see TECH.md)

- Not run (not requested).

## Review cycle 2 — changes-requested (2026-11-16)

- **Reviewed commit:** 085538c81869b4617fdbe8821dcb675c0227ee29  ·  **Base:** `origin/develop` bf03942d (merge base 91c5aa9a, #112)  ·  **Date:** 2026-11-16
- **Verdict:** changes-requested
- **Cycle:** 2 of ≤3 — mirrors `review.cycle` in `TECH.md`
- **Mode:** the default for a later cycle: a grade of cycle 1's F1–F4 remediation, then a fresh
  blind pass over the whole spec-excluded diff (`git diff origin/develop...HEAD -- . ':(exclude)spec/'`).

The blind reviewer was given `GOAL.md` (with its GOAL-gate block), the spec-excluded diff, the base
copies of `AGENTS.md` and `invariants.md`, `review-rubric.md`, cycle 1's findings by failure
scenario, and as contract readings the plan-gate rulings, the maintainer-signed review-cycle-1
rulings and the Non-goal amendment recorded with them (the relay's site fence and `--ssh --env` now
recognise key and relay variables by the matcher). It did not read PLAN, TECH, research, META, this
file or `red.txt`. The orchestrator read TECH's "Review-cycle-1 remediation" only to judge the
builder's deviations (below). The contract-drift check found only the shaping commit (2e92b87c) and
the GOAL-gate commit (94319cdb) on `GOAL.md`.

### Verification run

- Full suite, serial, at 085538c8 (`.venv/bin/python -m pytest -q -p no:cacheprovider`, sandbox
  off) → **3479 passed, 138 skipped** (24m58s); `test_waits_for_slow_peer_upgrade` passed in the run.
- PostgreSQL and TimescaleDB 2.32 on a local server (`HS_TEST_POSTGRES`, `HS_TEST_TIMESCALE=community`:
  `test_timescale`, `test_zone_postgres`, `test_writeback_postgres`, `test_contended_write`), whose
  fixtures configure the database through `HYPERSHELL_DATABASE_*` → 168 passed, 2 skipped, 1 failed:
  `test_legacy_database_upgraded_not_converted` needs the 2.8.3 database template only the Docker
  fixture builds, and fails the same way on the base. CI covers it.
- Reviewer's targeted runs: the schema, environment, blame, duration, export, completion and
  failed-launch files → 274 passed; `test_relay_launch` + `test_period_floor` → 220 passed;
  `test_cluster_launch` + `test_env_export` → 167 passed, 1 skipped.
- Mutation check: with each remediation undone in a scratch copy on `PYTHONPATH`, the new launch
  tests fail in 11 of 11 cases and the matcher tests in 19 of 20 (the one that passes is the
  canonical-name case, which the base already covered).
- CLI drives in throwaway sites: 40 `hs cluster` runs (launcher, `--mpi`, autoscaling, `--ssh`,
  `--ssh --env`; each with the queue key given by its canonical variable, `_ENV`, `_EVAL` and another
  spelling, for `server.auth` and for `relay.upstream.auth`), 8 upstream `hs server` + `hs relay`
  runs with the same forms, and about 60 base/branch comparisons of empty and typed values.
- A side-by-side environment-layer probe of 124 `HYPERSHELL_*` names used in the repo, docs,
  Dockerfile and Apptainer recipe: 110 identical to the base; the 14 that differ are the ruled
  changes (`_ENV`/`_EVAL`, process variables, unmatched names, open-table entries, `[export]` names
  as written, boolean words).
- `sphinx-build -W --keep-going -E -b html docs …` (Python 3.12 docs environment) → exit 0, no warnings.
- `git merge-tree --write-tree HEAD origin/develop` → clean (see **Merge status**).

### Cycle-1 findings

| Finding | Status | Evidence |
|---|---|---|
| F1 — launched clients refused for a key given in another form | **Remediated** | Every one of the 40 cluster runs and 8 relay runs completed its tasks. Each launched client held only the run's key, in one variable; ssh's own environment and `--env` exports held no key variable; no task held any form of the key; site clients held only the site key, and no relay variable reached a site client or task. Tests: `test_key_in_any_form_reaches_launched_clients` (9 cases), `test_client_launch_gives_only_its_own_key`, `test_ssh_launch_exports_no_key_variable`, `test_relay_fence_by_matcher`, `test_key_variable_forms_not_inherited`/`_not_exported`. |
| F3 — empty `database.user`/`password` from a file | **Remediated** | `data/core.py` is as on the base. A file's empty `user` or `password` gives the base's outcome (SQLite: "Cannot provide 'user'/'password' for SQLite"; PostgreSQL: the base's refusal or connection attempt); the environment form matches the base too. |
| F2 — empty optional text from the environment | **Remediated** (but see N1) | `HYPERSHELL_LOGGING_FILE=`: no traceback, no log file. `HYPERSHELL_DATABASE_HOST=` / `_DATABASE_DATABASE=` on SQLite: exit 0. `HYPERSHELL_SERVER_BIND=`: listens on `localhost` (base: exit 5). `HYPERSHELL_SERVER_TLS_CERT=`: the default materials. The guide states the rule. |
| F4 — seed line numbers | **Remediated** | Every line the two seeds cite holds the code they name at 085538c8. |

**Builder deviations judged** (TECH "Review-cycle-1 remediation"): `core/tls.py` and
`core/logging.py` back as on `develop` — sound: the environment layer now leaves those empties
unset, and a file reads as before (files identical to the base). Key variables left out of ssh's own
environment on an `--ssh` launch — sound and inside the F1 ruling (ssh is the launched process);
every `--ssh` drive completed. `is_key_variable(sections=…)` for the client's no-key hint,
`is_fenced` as the prefix or the matcher, and the eight #112 expectations — as ruled.

### Requirement → evidence matrix

| R-ID / ruling | Status | Evidence (cycle 2) |
|---|---|---|
| R1 | ✅ | 124-name probe; `server.tls.min_version`, `autoscale.failed_launches` reached and listed |
| R2 | ✅ | zone `gpu_a100` `launcher_args`; a zone variable with no zone key, and zone + zone table, refused naming the variables |
| R3 | ✅ | two forms in the environment exit 3; `_ENV` works for a hand-started `hs client`; `HYPERSHELL_SERVER_EVICT_ENV`/`_EVAL` resolve |
| R4 | ❌ N1 | empty `[export]` value kept, `RunId=007`, all-digit key; an empty `database.timescale.segmentby` is not held |
| R5 | ❌ N1 | empty boolean or number refused naming the variable; `LOGGING_FILE=Enabled` gives the default file; `segmentby`'s documented `""` unreachable from the environment |
| R6 | ✅ | file `evict = "10m"` serves (base: `TypeError`); bad `client.heartrate`, `relay.poll`, `relay.upstream.backoff` exit 3 before binding; option-backed values still exit 2 |
| R7 | ✅ | `HYPERSHELL_EXPORT_RunId=007` reaches tasks as `RunId=007`; an empty value stays empty |
| R8 | ✅ | `hs config which` names variables as written; the four process variables are not offered |
| R9 | ❌ N1, N2 | docs build clean; markers present; two claims of `config_intro.rst` do not hold (N1, N2) |
| F1 ruling, key and relay handling by the matcher | ✅ | drives and tests above |
| F2, F3, F4, wording, #112 | ✅ | above; no key-handling text singles out letter case |

Unmapped: N2. The `hs config edit` paragraph moved into `config_intro.rst` (same page) is harmless.

### Findings

#### N2 [HIGH/CONFIRMED] A file's empty `task.cwd` now runs tasks in the client's directory, with no prose or marker, and the guide says the opposite (§12; unmapped)
- **Where:** `src/hypershell/client.py:512` (`config.task.cwd or os.getcwd()`) and `:834`
  (`cwd=config.task.cwd or None`); `docs/_include/config_intro.rst:151` ("In a file, an empty
  value is read as before"); `docs/_include/config_param_ref.rst` `.cwd` entry.
- **Failure scenario:** a configuration file with `[task] cwd = ""`. Base: every task fails with
  `FileNotFoundError` (`hs cluster` exits 5, or does not finish). Branch: tasks run in the client's
  working directory and `hs cluster` exits 0. The reader treats empty text as unset in both layers,
  so the file layer changed. No R-ID or ruling asks for it (the F3 ruling keeps the file layer as on
  the base), the `.cwd` entry carries no prose or `versionchanged`, and the guide states that a
  file's empty value reads as before.
- **Evidence:** `temp_site.sh sh -c 'printf "[task]\ncwd = \"\"\n" > cfg.toml; printf "pwd\necho two\n" > tasks.in;
  HYPERSHELL_CONFIG_FILE=$PWD/cfg.toml hs cluster tasks.in --no-db --no-tls -N1'` → branch: the
  site path and `two`, exit 0; base (`PYTHONPATH=<base>/src`): `CRITICAL … FileNotFoundError: [Errno 2]
  No such file or directory: ''` from `TaskExecutor`. Reproduced by the reviewer and the orchestrator.
- **Assessment:** the new behaviour is the better one. Either keep it and document it (prose and
  `.. versionchanged:: 2.9.0` on `.cwd`, and an exception in the guide's sentence), or confine it to
  the environment (`task.cwd` in `EMPTY_UNSET`, the reader as on the base). The maintainer chooses.
- **Touches:** AGENTS.md §12 (same-commit docs rule); R9; scope; `client.py` (high-blast-radius).

#### N1 [MEDIUM/CONFIRMED] An empty `HYPERSHELL_DATABASE_TIMESCALE_*` variable is dropped, so `segmentby`'s documented `""` cannot be given from the environment (R4, R5, R9)
- **Where:** `src/hypershell/core/config_schema.py:152-153` (`EMPTY_UNSET` lists
  `database.timescale.chunk_interval`, `segmentby`, `orderby`, `compress_after`);
  `tests/test_config_schema.py:343-351` (`test_empty_unset_keys`).
- **Failure scenario:** `HYPERSHELL_DATABASE_TIMESCALE_SEGMENTBY=` is meant to turn segmenting
  off (`docs/_include/database.rst`: comma-separated columns, "or `""` for none"). The branch drops
  it: the command uses `client_id`, or a file's value when one is set. An empty `orderby`,
  `chunk_interval` or `compress_after` from the environment is silently replaced by the default,
  where the same empty value in a file is refused. These four keys have documented defaults
  (`data/timescale.py` `Settings`), so by the guide's own definition (`config_intro.rst:147`, "no
  default or one that means none") they are not optional and their empty value should be held as
  written. The test decides "optional" from `core/config.py`'s `default` table, which has no
  `database.timescale` entries, so it lets them in.
- **Evidence:** `Settings.from_config(config.database.get('timescale'))` with
  `HYPERSHELL_DATABASE_TIMESCALE_<KEY>=` → branch: table `None`, `segmentby='client_id'`,
  `orderby='id DESC'`, `chunk_interval='1 week'`, `compress_after='4 weeks'`; base: refused
  ("expected a string", or an unknown nested setting). File `segmentby = "id"` plus the empty
  variable gives `'id'`; file `segmentby = ""` gives `''`.
- **Assessment:** not a regression (the base refused these empties), but R4 ("an empty value"),
  R5 (every documented value kept) and the guide's rule are not met for these keys. Taking the four
  out of `EMPTY_UNSET`, and teaching the test the defaults `data/timescale.py` declares, meets them:
  `""` reaches `segmentby` as none, and the others are refused by their reader as in a file.
- **Touches:** R4, R5, R9; the F2 ruling's "optional text key".

#### N3 [LOW/CONFIRMED] `invariants.md` states the empty-variable rule; `AGENTS.md` does not
- **Where:** `.agents/factory/invariants.md:206-207` ("An empty variable for a key of `EMPTY_UNSET`
  is not given"); `AGENTS.md` § Configuration has no counterpart.
- **Evidence:** `grep -n -i 'EMPTY_UNSET\|empty variable\|not given\|empty value' AGENTS.md` → no
  match. `invariants.md` is the curated subset of `AGENTS.md`, kept in lockstep with it; every other
  edit to the two files on this branch agrees.
- **Touches:** the lockstep rule in `invariants.md`'s header.

### Merge status

- **`origin/develop` (bf03942d):** merges cleanly (`git merge-tree --write-tree HEAD origin/develop`,
  exit 0). Develop moved past the merge base only by `ROADMAP.md` and `issues/` (seed retirements
  and two new seeds); on the merged tree the seeds this branch adds or re-statuses, and the seed its
  ROADMAP entry points to (`client-bundlesize-config-key.md`), are present and linked.
- **#112 (merged in 092c80c0):** the merged `ServerApp`/`RelayApp` checks read coherently;
  `test_period_floor` (with the eight `env-nan`/`env-neginf` cases now refused as not a duration,
  exit 2, nothing served or launched) and `test_relay_launch` → 220 passed.

### Human-gate triggers

- **Triggered.** N2 is located in `client.py`, a high-blast-radius file. Its remedy may be
  docs-only, but the rubric's gate applies to the finding's location; human sign-off is required
  before the remediation proceeds.
- N1 and N3 touch no high-blast-radius file and no security or DB-lifecycle invariant.
- Cycle 1's trigger (F1) was discharged by the maintainer's sign-off on the review-cycle-1 rulings,
  and F1 is remediated.
- **Loop bound:** this is cycle 2 of at most 3. One more build↔review cycle remains; escalate to the
  maintainer if it does not converge.

### Optional completeness sub-pass

- Not run (not requested).

## Review cycle 3 — approved (2026-11-21)

- **Reviewed commit:** cb5e122b7c6c7e7471d30a98a2ff4771dff14d0b  ·  **Base:** `origin/develop` bf03942d (merge base 91c5aa9a)  ·  **Date:** 2026-11-21
- **Verdict:** approved
- **Cycle:** 3 of ≤3 — mirrors `review.cycle` in `TECH.md`
- **Mode:** the default for a later cycle: a grade of cycle 2's N1–N3 remediation, then a fresh
  blind pass over the whole spec-excluded diff (`git diff origin/develop...HEAD -- . ':(exclude)spec/'`).

The blind reviewer was given `GOAL.md` (with its GOAL-gate block), the base copies of
`review-rubric.md`, `invariants.md` and `AGENTS.md`, cycle 2's findings by failure scenario, and as
contract readings every ruling at the end of `PLAN.md` (plan gate; review-cycle-1 with the Non-goal
amendment; review-cycle-2; the `database.host` note), copied out of `spec/` so that it read nothing
there. It did not read PLAN, TECH, research, META, this file or `red.txt`. The orchestrator read
TECH's "Review-cycle-2 remediation" only to judge the builder's deviations (below). The
contract-drift check found only the shaping commit (2e92b87c) and the GOAL-gate commit (94319cdb)
on `GOAL.md`.

### Verification run

- Full suite, serial, at cb5e122b (`.venv/bin/python -m pytest -q -p no:cacheprovider`, sandbox
  off) → **3488 passed, 138 skipped** (25m40s); `test_waits_for_slow_peer_upgrade` passed in the run.
- Reviewer's targeted runs: the schema, environment, blame, duration, export, completion,
  failed-launch and period-floor files → 432 passed; the launch, `--ssh` and relay key-handling
  tests → 94 passed.
- Mutation check: 28 mutants in scratch copies on `PYTHONPATH` (worktree untouched) → 24 killed;
  of the 4 that survive, two are equivalent and one near-equivalent (no difference a user or a task
  can see), and one is G1.
- A side-by-side environment-layer sweep, base vs branch: 173 `HYPERSHELL_*` names (every key in
  the table and every name the repo, docs, tests, `share/`, Dockerfile, Apptainer recipe and CI use)
  × 6 values (1038 cases). Every variable that set a key on the base sets the same key; the
  underscore keys land at their own path; the other differences are the ruled changes.
- CLI drives in throwaway sites (`temp_site.sh`, `-H 127.0.0.1`): `task.cwd` from a file and from
  the environment (6 runs, both trees); 19 empty variables on both trees; the all-digit key through
  `hs server`, `hs submit --queue` and `hs client`; `hs relay` with every relay duration key in
  units; `[export]` and task-environment drives; `hs config which`/`get` and the completion list.
- Orchestrator probe (both trees): `HYPERSHELL_TASK_CWD=` with `HYPERSHELL_DATABASE_TIMESCALE_SEGMENTBY=`
  and `HYPERSHELL_DATABASE_HOST=` → branch: `task.cwd` the default, `segmentby` `''`, `host` unset
  (base: `None` for all three); a file's `cwd = ""` → `''` on both.
- `sphinx-build -W --keep-going -q -E -b html docs …` (Python 3.12 docs environment) → exit 0, no warnings.
- Merge checks (`git merge-tree --write-tree`) and a test run on the merged tree, see **Merge status**.

### Cycle-2 findings

| Finding | Status | Evidence |
|---|---|---|
| N2 — a file's empty `task.cwd` changed behaviour | **Remediated** | `client.py` reads `task.cwd` as on the base (`:512`, `:834`; no `cwd` hunk in its diff). `hs cluster tasks.in --no-db --no-tls -N1`: a file's `cwd = ""` gives the base's `FileNotFoundError` for `''` on both trees; `HYPERSHELL_TASK_CWD=` runs the tasks in the client's directory, exit 0 (base: `TypeError`); a file's `cwd = "/tmp"` with the empty variable runs in `/tmp`. Mutants on either layer are killed. |
| N1 — empty `database.timescale` variables dropped | **Remediated** | The four keys are out of `EMPTY_UNSET`. `HYPERSHELL_DATABASE_TIMESCALE_SEGMENTBY=` gives `segmentby = ''` (no segmenting), alone and over a file's value, as a file's `segmentby = ""` does; an empty `orderby` (and `chunk_interval`, `compress_after`) is refused by `Settings.from_config` with the file's message (base: "expected a string"). `test_empty_unset_keys` decides "optional" from the documented defaults; putting the keys back fails 8 tests. |
| N3 — `AGENTS.md` lacked the empty-value rule | **Remediated** | `AGENTS.md` § Configuration states it beside `invariants.md` § 10; both agree with `config_intro.rst` and `manual.rst`. |

**Builder deviations judged** (TECH "Review-cycle-2 remediation"): `EMPTY_IS_DEFAULT` in the schema
test (`server.bind`, `relay.bind`, `database.host`, `task.cwd`) — as ruled. The reference-reading
`documented_default` helper — sound; it is what the N1 ruling asks the test to use. The orchestrator
checked every remaining `EMPTY_UNSET` member against the defaults table and the reference: each has
no default, one that means none (`<none>`, `<auto>`), or one given only in words (a provider's
schema, a path pattern, the style's format), so the classification matches the guide's rule.

### Requirement → evidence matrix

| R-ID / ruling | Status | Evidence (cycle 3) |
|---|---|---|
| R1 | ✅ | 1038-case sweep; `server.tls.min_version`, `database.timescale.chunk_interval`/`compress_after`, `relay.launcher_args`, `relay.num_threads`, `relay.upstream.evict_hint`, `autoscale.failed_launches` land at their own path; `hs config which autoscale.failed_launches` names its variable |
| R2 | ✅ | `database.pragmas.journal_mode`, `ssh.nodelist.my_group`, zone `gpu_a100` `launcher_args`; a zone variable with no zone key, and zone + zone table, refused naming the variables |
| R3 | ✅ | `hs config get server.auth` via `_ENV` shows the resolved key (base: a table); `_EVAL` resolves; two forms in the environment layer exit 3 for `server.auth` and `relay.upstream.auth` |
| R4 | ✅ | key `0012345678901234`: branch `hs server` serves, `hs submit --queue` submits, `hs client` runs the tasks; base `hs server` refuses it as too short |
| R5 | ✅ | `HYPERSHELL_SERVER_PORT=abc` exits 3 naming the variable and form; `HYPERSHELL_SERVER_EAGER=no` reads false |
| R6 | ✅ | bad `server.evict` (file, environment), `client.heartrate`, `relay.poll` exit 3 before binding, naming key, source and forms; `hs relay` with the relay keys in units runs (base: `TypeError`, exit 5); #106's options keep exit 2. Coverage gap: G1 |
| R7 | ✅ | a task sees `RUN_ID=007`, `MyVar=x`, an empty value as empty, `1e3` as written; the environment wins over a file; a file's table as before |
| R8 | ✅ | `hs config which` names the variable as written; the completion list has the underscore keys and no `site`, `config.file`, `pythonpath`, `zone` or dotted non-keys |
| R9 | ✅ | docs build clean; the guide, reference, task-environment, manual and security pages checked against the drives; `versionchanged:: 2.9.0` markers present |
| Plan-gate S1–S13 | ✅ | the ten keys exit 3, #106's exit 2; typed values refused at import; an empty number variable refused |
| Cycle-1 rulings (F1 key handling, the Non-goal amendment, F2–F4, wording, #112) | ✅ | no form of the key reaches a launched client's environment beside the run key, an `--env` export or a task; F3's file case pinned; seed citations hold at HEAD; `test_period_floor` passes |
| Cycle-2 rulings (N1–N3, wording) and the `database.host` note | ✅ | above |

Unmapped changes: none. `hs config --list-available` printing one key per line (R8), the `hs config edit`
paragraph moved into `config_intro.rst` (R9 layout), `plain_seconds` for `-w`/`-T` and the relay's
`DEFAULT_*` constants reading `default` (R6), and the two seeds are all accounted for.

### Findings

#### G1 [LOW/CONFIRMED, coverage only] No test pins the relay handing its converted durations to its parts (R6)
- **Where:** `src/hypershell/relay/__init__.py:1149-1173` (`RelayApp` builds `RelayThread` from
  `self.durations[…]`).
- **Scenario:** a later edit that passes a relay duration key to `RelayThread` as read
  (`evict=config.server.evict` in place of `self.durations['server.evict']`) would bring back the
  base's `TypeError` for a unit value, and no test would fail. The tests pin `config_seconds` and the
  refusal at start, but none starts the relay with a valid unit value.
- **Evidence:** that mutant in a scratch copy on `PYTHONPATH` → `tests/test_env_duration.py` passes
  in full. The code itself is correct: `hs relay` with `server.evict = "10m"` and the other relay
  keys in units runs until stopped (base: `TypeError: '>=' not supported between instances of 'str'
  and 'int'`, exit 5).
- **Grading:** LOW is the rubric's row for "missing-but-non-blocking test coverage of an R-ID", and
  no behaviour is wrong, so it does not block. Suggested follow-up: one test that starts the relay
  (or `RelayApp`) with each relay duration key in units and checks the seconds its parts receive.
- **Touches:** R6 (test strength only). Not a high-blast-radius file.

**Considered and dropped:** an empty variable for a duration key that has an option
(`HYPERSHELL_SERVER_POLL=`, `_SUBMIT_BUNDLEWAIT=`, `_TASK_SIGNALWAIT=`) is now refused by the option's
parser with exit 2, where the base read it as `None`. This is the ruled rule (a key with a default
of its own holds an empty value as written, and a duration is checked by the command that reads it),
and it matches a file's `""` on both trees.

### Merge status

- **`origin/develop` (bf03942d):** merges cleanly (`git merge-tree --write-tree origin/develop HEAD`,
  exit 0). Develop moved past the merge base only in `ROADMAP.md` and `issues/`.
- **`fix/cluster-launch-followups`:** not on `origin`; checked against the local branch (c29f75a0,
  based on develop 9c82842d). It merges cleanly with this branch, and the three-way tree (develop,
  that branch, this one) is clean. The overlapping files (`ROADMAP.md`, `cluster/remote.py`,
  `server.py`) change disjoint code: that branch skips an autoscale check on a locked database and
  counts the receiver's writes; this one reads `autoscale.failed_launches` from its own key and
  `server.evict`/`client.heartrate` through `config_seconds`. On the three-way tree, both branches'
  new and touched test files (`test_cluster_locked`, `test_cluster_nodb_finish`, `test_cluster_no_db`,
  `test_cluster_launch`, `test_autoscale_failed_launches`, `test_period_floor`, `test_relay_launch`,
  `test_config_schema` and the four `test_env_*` files) → 697 passed, 4 skipped.

### Human-gate triggers

- **Not triggered this cycle.** The only CONFIRMED finding, G1, is a coverage gap in
  `relay/__init__.py`, which is not a high-blast-radius file, and touches no security or
  DB-lifecycle invariant. The high-blast-radius hunks (`client.py`, `server.py`, `cluster/remote.py`)
  were re-examined and driven; nothing was found in them.
- Cycle 2's trigger (N2, `client.py`) was discharged by the maintainer's sign-off on the
  review-cycle-2 rulings, given before the remediation; N2 is remediated with the reader as on the
  base. Cycle 1's trigger (F1) was discharged in cycle 2.
- **Loop bound:** cycle 3 of at most 3, and it converged.

### Optional completeness sub-pass

- Not run (not requested).
