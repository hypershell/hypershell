# Research 02 — tests for client-host-default-from-bind

Worktree HEAD `b88a09ae`. Probes were run in a child interpreter with `/tmp/claude-501/venv314-host`
(editable install of this worktree).

## Findings

1. **Use a child interpreter for anything read at import.** `config` is built once at import
   (invariants §10). `ClientApp.host` (`client.py:1657`) and `SubmitApp.queue_server`
   (`submit.py:1614`) are class attributes, so the existing pattern is to run a fresh interpreter:
   - `config_repr(site, expr, imports, **variables)` at `tests/test_env_config.py:84` runs
     `python -c` with `cwd=site`. Its environment comes from `child_env` (`:78`):
     `HYPERSHELL_SITE=site`, `HYPERSHELL_CONFIG_FILE=''`, plus the given variables, and a value of
     `None` unsets a variable. It returns `(rc, repr(expr), stderr)`. `tests/test_env_blame.py:24`
     and `tests/test_config_unread.py:30` already import it.
   - `file_layer(site, text)` at `tests/test_config_unread.py:36` writes `site/config.toml` and
     returns `{'HYPERSHELL_CONFIG_FILE': path}`. Setting `HYPERSHELL_CONFIG_FILE` makes that file the
     only file layer (`core/config.py:575`, `reload_local`, layer `local`), so the system and user
     files are never read.
   - `tests/conftest.py:59` (`isolate_environment`) removes every `HYPERSHELL_*` variable, blanks
     `HYPERSHELL_CONFIG_FILE` and pins a free `HYPERSHELL_SERVER_PORT`. The `temp_site` fixture
     (`:83`) adds `HYPERSHELL_SITE` and the database file. Use `temp_site` with
     `config_repr`/`file_layer`.
   - Batching several readers into one expression is the existing practice (`RETRY_READ`,
     `test_config_unread.py:49`). One child that builds `ClientApp`, `SubmitApp`, `ServerApp`,
     `ClusterApp` and `QueueConfig.load()` took **0.35–0.65 s**. `tests/test_env_blame.py` (6 tests,
     about 7 children) ran serially in **2.8 s**.
2. **Unit tests against a built configuration.** `layered(**layers)` at
   `tests/test_client_bundle_keys.py:31` builds
   `Configuration(default=default, **{name: Namespace(data)})`. The same helper is at
   `test_client_request.py:163`, `test_auth_variants.py:44` and `test_relay_launch.py:229`.
   `which()` works on these configurations. `test_cluster_advertise.py:130` patches the module's
   `config` with a plain `Namespace`, which has no layers. **Do not copy it**: the rule depends on
   `which()`.
3. **A variant form breaks a bare `which()` (a risk for the helper; verified).** With only
   `host_env` (or `host_eval`) given, `config.server.host` resolves (for example
   `HYPERSHELL_SERVER_HOST_ENV=XH XH=login00` gives `'login00'`). But
   `config.which('server','host')` **raises `KeyError`** (`_displaced`), and
   `which('server','host_env')` returns that form's layer. The helper must check the forms the way
   `advertised_size`/`_source` do (`client.py:364-385`, a loop over `host`, `host_env` and
   `host_eval`, with `KeyError` read as not given). Tests must cover both forms. The environment
   matcher accepts `_ENV`/`_EVAL` for every key (`config_schema.py:286`).
4. **Behaviour on HEAD, from one child, with the seed file** (`bind="0.0.0.0"`,
   `host="login00.example.edu"`, `advertise=""`):
   - client default `0.0.0.0`; client with `-H x.y` gives `x.y`;
   - submit default `login00.example.edu`; `QueueConfig.load().host` `login00.example.edu`;
   - `DEFAULT_HOST` (client and submit) and `QueueConfig().host` are `localhost`;
   - the `ServerApp`/`ClusterApp` `-H` default is `0.0.0.0`;
   - `which(host)` and `which(advertise)` both give `local`.
5. **Strict-xfail precedent** (`spec/relay-client-exe-deprecated-name/TECH.md:180-183`, P1
   `:187-247`):
   - Each test that needs the fix carries
     `pytest.mark.xfail(strict=True, reason='relay-remote-exe P2')`, or a single row carries it
     through `pytest.param(..., marks=…)`.
   - A missing function is imported with a guard (`x = getattr(module, 'x', None)`) so that
     collection survives.
   - The phase that fixes a test removes its marker. That phase's verify runs
     `! grep -rq 'relay-remote-exe P2' tests`, and P1's verify greps the log for named `XFAIL`
     lines and `! grep -q XPASS`. (The markers are gone from `tests/test_relay_remote_exe.py` now,
     after the squash in #121.)
6. **`bound.sh` exists** at `spec/relay-advertise/drive/bound.sh`: `sh bound.sh SECONDS CMD…`. It
   runs the command in its own process group, sends SIGINT at the bound, then SIGKILL 15 s later,
   and exits 124 at the bound. It needs only perl, since macOS has no `timeout`.
   `.agents/factory/bin/temp_site.sh` sets `HYPERSHELL_SITE`, `HYPERSHELL_DATABASE_FILE` and
   `UV_PROJECT` and changes into the site directory, but it does **not** blank
   `HYPERSHELL_CONFIG_FILE`. A help drive through it must set `HYPERSHELL_CONFIG_FILE=` itself, or
   the developer's own configuration leaks into the output.

## Proposed test matrix — `tests/test_client_host_default.py`

**Unit tests (U).** Call the helper on `layered(...)`, about 20 rows, under a guarded import. They
are instant.

**Child tests (C).** `config_repr` evaluates `ClientApp.from_cmdline([]).host`,
`SubmitApp.from_cmdline(['-q','echo','one']).queue_server` and `QueueConfig.load().host`.

- Parametrize the child tests by **reader × case**. Each reader's HEAD result differs, so each
  (reader, case) row gets its own strict xfail (see the HEAD column).
- About 14 cases × 3 readers, at about 0.5 s each, comes to **about 25 s**. The whole file should
  take **under 40 s serially**.
- A cheaper option is one child per case, cached for each module, about 8 s.

| case | config given | expected (all 3 readers) | kind | passes on HEAD? |
|---|---|---|---|---|
| seed (R1) | file `bind="0.0.0.0"`, `host="login00.example.edu"` | `login00.example.edu` | U+C | client xfail; submit/queue pass |
| seed by env | `SERVER_BIND=0.0.0.0`, `SERVER_HOST=login00…` | `login00.example.edu` | C | client xfail |
| -H wins (R2) | file host=a, advertise=b, bind=10.0.0.5; argv `-H x.y` | `x.y` (client and submit) | C | pass |
| host over advertise | env `SERVER_ADVERTISE=adv.example`, file `host="login00…"` (layer differs) | `login00.example.edu` | U+C | client xfail |
| advertise over specific bind | file `bind="10.0.0.5"`, `advertise="adv.example"` | `adv.example` | U+C | all xfail |
| advertise by env | `SERVER_ADVERTISE=adv.example`, bind 0.0.0.0 | `adv.example` | U+C | all xfail |
| specific bind | env `SERVER_BIND=10.0.0.5` | `10.0.0.5` | U+C | client pass; submit/queue xfail |
| wildcard bind | file `bind="0.0.0.0"` | `localhost` | U+C | client xfail; others pass |
| empty bind (file) | file `bind=""` | `localhost` | U+C | client xfail (HEAD gives `''`) |
| empty bind (env) | `SERVER_BIND=''` (empty means unset) | `localhost` | C | pass |
| nothing given | — | `localhost` | U+C | pass |
| host given as `localhost` | file `host="localhost"`, `advertise="adv.example"`, `bind="10.0.0.5"` | `localhost` | U+C | client xfail; others pass |
| advertise empty/marker | file `advertise=""` or `"<none>"`, bind 10.0.0.5 | `10.0.0.5` | U+C | client pass; others xfail |
| host_env form | `SERVER_HOST_ENV=CHD_HOST`, `CHD_HOST=login00…`, bind 0.0.0.0 | `login00.example.edu` | U+C | client xfail |
| host_eval form | file `host_eval="echo login00.example.edu"` | `login00.example.edu` | U | — |
| advertise_env form | `SERVER_ADVERTISE_ENV=CHD_ADV`, bind 10.0.0.5 | value of `CHD_ADV` | U | — |
| API defaults (R5) | seed file plus `SERVER_ADVERTISE` | client/submit `DEFAULT_HOST`, `QueueConfig().host`, `ClientThread.__init__` address default all `localhost` | C (one child) | pass |
| server side (R6) | seed file | `ServerApp.from_cmdline([]).host` = `ClusterApp.from_cmdline(['t.in']).host` = `0.0.0.0` | C (one child) | pass |
| help/docs (R8) | — | `-H` entry in client and submit `APP_HELP`, `client_help.rst` and `submit_help.rst` names `server.host`. Config reference `.host`, `.advertise` and `.bind` (`parse_reference`) state the rule and carry `versionchanged:: 2.9.0` | U | xfail (docs phase) |

Use `@mark.unit` for U and docs rows and `@mark.integration` for C rows, the same split as
`test_config_unread.py`. Use only addresses that never resolve, such as `*.example.edu`,
`adv.example` and `10.0.0.5`: nothing dials.

## Tests that will change

**None found that asserts the old behaviour.**

- There is no test of `ClientApp.host`, `DEFAULT_HOST`, `QueueConfig().host`,
  `QueueConfig.load()`, `queue_server` or `"(default: localhost)"`.
- `test_main.py:73` and `test_submit.py:36` compare `--help` output with `interface.help_text`,
  which is the same string, so they are not affected.
- No test compares `client_help.rst` or `submit_help.rst` with `--help`. Only the relay help pages
  are checked (`test_relay_advertise.py:230`, `test_relay_remote_exe.py:444`).
- `test_config_schema.py`:
  - `test_documented_default` (`:372`) does not name `server.host`.
  - `test_empty_unset_keys` (`:384`) stays green **only if `default['server']['host']` stays
    `'localhost'`**. If it became `<none>`, `server.host` would count as optional text, and the test
    would then require it in `EMPTY_UNSET`, which contradicts the Non-goal.
  - Writing `.host`'s reference default in words is safe.
- Subprocess tests that start `hs client` without `-H` (`test_env_config.py:553`,
  `test_auth_variants.py:311`, `test_server_initdb.py:272`, `test_env_duration.py:109`, and
  others) give no bind, host or advertise, so they still dial `localhost`.
- `test_cluster_advertise.py:230` sets `HYPERSHELL_SERVER_ADVERTISE` for a local cluster, which
  dials `localhost` explicitly. The relay `server-key` case (`test_relay_advertise.py:198`) passes
  `-H` to its clients. Neither is affected.

## Verify-command suggestions

Export these first: `UV_PROJECT_ENVIRONMENT=/tmp/claude-501/venv314-host`,
`PATH=/tmp/claude-501/venv314-host/bin:$PATH`, `UV_CACHE_DIR=/tmp/claude-501/uv-cache-chd` (new;
the shared cache was corrupt in a prior cycle) and `TMPDIR=/tmp/claude-501`. Run everything
serially, never with `-n`.

- **P1 (tests only):**
  `l="$TMPDIR/chd-p1.log"; test -z "$(git diff --name-only b88a09ae -- . ':!spec' ':!tests')" && sh spec/relay-advertise/drive/bound.sh 300 uv run pytest -q -p no:cacheprovider -rxX tests/test_client_host_default.py > "$l" 2>&1; r=$?; tail -n 25 "$l"; test $r -eq 0 && grep -q 'XFAIL.*seed' "$l" && ! grep -q XPASS "$l"`
- **Fix phase:**
  `sh …/bound.sh 900 uv run pytest -q -p no:cacheprovider -rxX tests/test_client_host_default.py tests/test_config_schema.py tests/test_env_config.py tests/test_env_blame.py tests/test_config_unread.py tests/test_cluster_advertise.py tests/test_client_bundle_keys.py tests/test_client_request.py tests/test_submit.py tests/test_main.py > "$l" 2>&1`, then `! grep -rq 'client-host-default P2' tests`.
  - Optional help drive:
    `sh …/bound.sh 120 .agents/factory/bin/temp_site.sh env HYPERSHELL_CONFIG_FILE= sh -c 'uv run hs client --help' | grep -A3 -- '--host' | grep -q server.host`.
    Do the same for `hs submit --help`.
- **Docs phase:** the precedent's `sphinx-build -W` in a docs venv of this cycle's own. It needs
  the sandbox off (sitemap socket bind).

## Open risks

- The guarded import is needed because the helper's name and module are not yet chosen. Pick them
  in the TECH before P1. A helper that takes the configuration as an argument is unit-testable with
  `layered()`. One that reads a module global needs `monkeypatch.setattr` on that exact module.
- Reusing `WILDCARD_BINDS` from `cluster.launch` in core code may create an import cycle. If the
  set is moved, `test_wildcard_binds` (`test_cluster_advertise.py:73`) must follow the move.
- An empty `host` given by a file (`host = ""`) has no row in the GOAL. By the "a layer gives it"
  rule it would dial `''`. Decide this and pin it with a test.
- `hs config get server.host` still prints `localhost` when nothing gives it. This is documented,
  not tested.
