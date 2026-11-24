# PLAN — `hs client` dials `server.host` when no `-H` is given

> **Status:** Draft for review · **Last updated:** 2026-11-23
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). Backing detail is in
> [`research/`](research/). Every design element traces to a GOAL R-ID.

## 1. Summary

One function, `client_host()` in `core/config.py`, states the ruled rule. With no `-H`, a client
dials the first of:

1. `server.host`, if a layer gives it;
2. `server.advertise`, if a layer gives it;
3. `server.bind`, if it is one specific address;
4. `localhost`.

"Given" is decided by the configuration layer that holds the key's form, not by its value.
`hs client` and `hs submit --queue` default `-H` to `None` and call the function when they check
their arguments. `QueueConfig.load()` calls it directly. The Python API defaults keep their built-in
`localhost`, now taken from `server.host` and never from `bind`.

The work is three phases:

- **P1:** the tests, first.
- **P2:** the rule, with its help, docs, completion and agent docs.
- **P3:** the release note and the full gate.

The appetite stays small: one function, three readers, two default sources, and docs.

## 2. Design

### 2.1 The rule (`src/hypershell/core/config.py`) — R1, R3, R4

```python
WILDCARD_BINDS: Final[FrozenSet[str]] = frozenset({'0.0.0.0', ''})
"""Bind addresses that listen on every interface, which no client can dial as such."""

def client_host(base: Configuration = None) -> str:
    """The address a client given no -H dials: server.host, else server.advertise, else
    server.bind when it is one address, else localhost (see the [server] reference)."""
```

- **Where it lives.** `base` defaults to the module's `config`, as `config_seconds` and
  `resolve_auth` do. Tests pass a constructed `Configuration`, and the import-time singleton is
  never reloaded (invariants §10).
- **`WILDCARD_BINDS`.** It moves here from `cluster/launch.py`, which imports it back and keeps it
  in `__all__`. `advertised_host` is unchanged
  ([`research/01`](research/01-config-rule.md) F7).
- **Given.** A private `_given(base, name) -> Optional[str]` returns the deciding form, or `None`.
  - It finds the first of `name`, `name_env` and `name_eval` present in the merged `base['server']`.
    The merged view holds only the winning layer's variants. This is the same lookup as
    `config_seconds`.
  - It then calls `base.which('server', form)`. `'default'`, or a `KeyError`, means not given.
  - It never calls a bare `which('server', 'host')`, which raises `KeyError` when a variant
    displaces the key (research/01 F2, research/02 F3).
- **Value.** The value is read by attribute (`getattr(base.server, name)`), so `_env` and `_eval`
  resolve. An `_env` form whose variable is unset resolves to `None`. That raises
  `ConfigurationError(f'server.{form} names {VAR}, which is not set ({blame(...)})')`, the message
  `config_seconds` gives.
- **Order.**
  1. `host` given and not `PARAM_UNSET` → `str(value)`. This holds even for `'localhost'`, and even
     for `''`, which the GOAL's non-goal holds as given.
  2. `advertise` given, with its value not in `(None, '', PARAM_UNSET)` → that value. A file's
     `advertise = ""` or `"<none>"` falls through, as in `ClusterApp.advertise_host`.
  3. `bind` (attribute, whether given or default) not in `WILDCARD_BINDS` and not `PARAM_UNSET` →
     that value.
  4. `'localhost'`, a literal.
- **`PARAM_UNSET` never reaches a client (asked by the gate).** `'<none>'` is the default only of
  `advertise`, and branch 2 excludes it. Branches 1 and 3 exclude it too, against a file that writes
  it literally. Branch 4 is a literal. A unit test asserts that no row returns `'<none>'`.
- **Not touched.** `default['server']['host']` stays `'localhost'`. Dropping it would make
  `test_empty_unset_keys` demand `server.host` in `EMPTY_UNSET`, against the non-goal
  ([`research/03`](research/03-docs.md) F2). No key, kind or `EMPTY_UNSET` entry changes.
- **Export.** `client_host` and `WILDCARD_BINDS` join `core/config.py`'s `__all__`.

### 2.2 The readers — R1–R4

- **`hs client`** (`client.py`).
  - `host: Optional[str] = None`, and `-H/--host` keeps `default=host`.
  - In `check_args` (called from `run()`, which already reads `client.heartrate` there):
    `if self.host is None: self.host = client_host()`.
  - `run()` dials `(self.host, self.port)` as today, and the `gaierror` hint names the resolved host.
- **`hs submit --queue`** (`submit.py`).
  - `queue_server: Optional[str] = None`.
  - In `check_arguments`: `if self.queue_mode and self.queue_server is None: self.queue_server =
    client_host()`.
  - Without `--queue` it stays `None` and is unused.
  - `run()` builds `QueueConfig(host=self.queue_server, …)` as today.
- **`QueueConfig.load()`** (`core/queue.py:1107`): `'host': client_host(_config)`.
- **Why at start, not at import.** Both option defaults are class attributes, evaluated at import
  for every `hs` command, because `hypershell/__init__.py` imports both apps.
  - At import, an unset `host_env` would make every command fail, `hs list` included.
  - Every `host_eval` command would run at every start; `submit.py` already does this today.
  - Resolving at start confines both to the command that dials ([`research/01`](research/01-config-rule.md)
    F8, risk 1).
- **Tests.** The child tests call `ClientApp.from_cmdline([…])` then `.check_args()` then `.host`,
  and `SubmitApp.from_cmdline(['-q', …])` then `.check_arguments()` then `.queue_server`. Both
  methods exist on HEAD, so each xfail row fails for the right reason.

### 2.3 The Python API and the server side — R5, R6

- `QueueConfig.host: str = default.server.host`, was `default.server.bind`. It is the same
  `'localhost'`, and no client-side default now names `bind`.
- `DEFAULT_HOST` in `client.py` and `submit.py` still reads `QueueConfig.host`. `ClientThread.new`,
  `run_client` and the `QueueConfig()` field are unchanged in value and never read the loaded
  configuration.
- `hs server`'s `DEFAULT_BIND` (`server.py:1142`) changes from `QueueConfig.host` to
  `default.server.bind`. It is the same `'localhost'`, and the server keeps its own built-in bind
  default.
- `ServerApp.host` and `ClusterApp.host` (`config.server.bind`), `advertised_host`,
  `ClusterApp.advertise_host` and the relay are untouched.
- `hs relay`'s upstream keeps `relay.upstream.host`.

### 2.4 Help, docs, completion, agent docs — R8

Per AGENTS.md § Docs and invariants §12, these go in the commit that changes the behaviour (P2).
Wording follows [`research/03`](research/03-docs.md).

- **Help strings** (`client.py:1625`, `submit.py:1543`). `-H` shows `(default: server.host, else
  server.advertise, else server.bind if one address, else localhost)`, wrapped to the description
  column. `{DEFAULT_HOST}` is no longer printed there, since it was never the effective default.
- **`docs/_include/client_help.rst`, `submit_help.rst`.** The `-H` entry gives `(default:
  ``server.host``)` and one sentence of the rule. Submit says "with `--queue`".
- **`docs/_include/config_param_ref.rst` `[server]`.**
  - `.host`: `(default: by ``.advertise`` or ``.bind``)`; says clients and `submit --queue` dial it
    when given in any configuration file or by `HYPERSHELL_SERVER_HOST`, with the fallback order,
    `-H` first, and the seed's shared-file example.
  - `.advertise`: adds that a client or `submit --queue` given no `-H` dials it when `.host` is not
    given.
  - `.bind`: adds when a client dials it.
  - Each gets a `.. versionchanged:: 2.9.0` in an `.. only:: html` block (`.advertise`'s is
    appended to its existing block), with no `:pr:`, which `hs-release` adds.
  - `test_documented_default` parses a worded default as `None` and passes.
- **Markers beside the generated help.** A `.. versionchanged:: 2.9.0` in `docs/cli/client.rst` and
  `docs/cli/submit.rst`, the last of each page's marker run, above the `.. include::`. Not in
  `manual.rst`, which follows the relay `--remote-exe` precedent.
- **Completion.** The `share/zsh/site-functions/_hs` `hs client -H` description says `(default:
  localhost)` today. It becomes `(default: by server.host)`. Bash completion carries no
  descriptions.
- **Agent docs.** AGENTS.md (the `advertised_host` paragraph) and `.agents/factory/invariants.md`
  §11 say only `ClusterApp`/`RelayApp` read `advertise`. Each gains one sentence: a client given no
  `-H` (`hs client`, `hs submit --queue`, `QueueConfig.load()`) dials
  `core/config.py::client_host`. A client-side default never reads `bind` directly, and the Python
  API defaults stay `localhost`.
- **Not changed.** `docs/security.rst`'s TLS example already gives clients `host = …`. It becomes
  correct with this fix, with no text change. Man pages are rebuilt by `hs-release`.

### 2.5 The release note — R8

`spec/client-host-default-from-bind/RELEASE-NOTE.md` holds the squash body, written in P3 from the
[`research/03`](research/03-docs.md) draft. It is plain text and names top-level commands only.

- **The fix.** One paragraph: what was broken (a client dialled `server.bind`), and the rule now.
- **What changes for whom.** A "Behaviour change:" paragraph:
  - A configuration that gives `server.host` now has `hs client` dial it.
  - One that gives `server.advertise` but not `host` now has `hs client`, `hs submit --queue` and
    `QueueConfig.load()` dial `advertise`.
  - One that gives only a specific `server.bind` now has `hs submit --queue` and `QueueConfig.load()`
    dial it, where they dialled `localhost`; `hs client` is unchanged there.
  - A wildcard bind with neither key given has `hs client` dial `localhost`, not `0.0.0.0` (the same
    host).
- **Also stated:**
  - `-H` and the Python API defaults are unchanged.
  - `server.advertise` keeps its meaning for the clients `hs cluster` launches.
  - A `host_eval` command is no longer run by every `hs` command at start, only by the command that
    dials.
  - An `_env` form naming an unset variable is now refused when the command starts, naming the key.

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1 | `client_host` branch 1 (§2.1); `ClientApp.check_args` resolution (§2.2) |
| R2 | `-H` given → `host` is not `None` → never resolved (§2.2) |
| R3 | `client_host` order, `_given` by form and layer, `PARAM_UNSET`/empty handling, `WILDCARD_BINDS` (§2.1) |
| R4 | `SubmitApp.check_arguments` and `QueueConfig.load()` call the same `client_host` (§2.2); one statement of the rule in the `.host` reference entry (§2.4) |
| R5 | `QueueConfig.host` from `default.server.host`; `DEFAULT_HOST` unchanged; relay upstream untouched (§2.3) |
| R6 | `DEFAULT_BIND` from `default.server.bind`; server, cluster, `advertised_host` untouched (§2.3) |
| R7 | `tests/test_client_host_default.py` (§6), P1 |
| R8 | §2.4, §2.5; P2 (docs with the code), P3 (release note) |

## 3. Invariant gate (AGENTS.md constitution check)

Checked against [`.agents/factory/invariants.md`](../../.agents/factory/invariants.md) before
research and again after this design.

- **Touched:** §9, §10, §11 and §12.
- **Untouched:** §1–§8. No task, retry, server-mode, FSM, shutdown, resource or signal change.

- **§10 Configuration.**
  - `config` stays an import-time singleton. It is read, never mutated.
  - The rule reads it when the command starts, as `check_args` already does for `client.heartrate`.
  - No key, kind, reference entry name or `EMPTY_UNSET` entry is added or removed, so
    `test_config_schema` is unaffected apart from the worded `.host` default it already accepts.
  - The `_env`/`_eval` forms resolve through the matcher and cmdkit as for every key.
  - `'<none>'` (unset) is never dialled (§2.1).
- **§9 Transport & security.**
  - Auth and TLS are unchanged: `resolve_auth('server')` and `enable_tls()` run as before.
  - A client that now dials `advertise` checks the server certificate against that name in the
    system-CA case, which is `advertise`'s documented purpose.
  - No key on any command line.
- **§11 Cluster orchestration.**
  - `advertised_host` and `client_launch` are unchanged. Launched clients still get an explicit
    `-H`, so `client_host` never runs for them, and the 2.9.0a4 contract test is unaffected.
  - `WILDCARD_BINDS` moves but stays importable from `cluster/launch.py`.
  - The §11 sentence "only `ClusterApp`/`RelayApp` read" `advertise` is amended in the same commit
    (§2.4).
- **§12 Conventions.**
  - The help strings, `*_help.rst`, completion, prose entries and markers go in one commit (P2).
  - New tests are tagged `@mark.unit` or `@mark.integration`.
  - Exit statuses are unchanged: a bad `_env` form raises `ConfigurationError`, which the
    application already maps.
  - Version strings are untouched.
- **High-blast-radius files.** `client.py` changes one option default and adds one line in
  `check_args`. `core/queue.py` changes one field-default source and one `load()` line. `server.py`
  changes one constant's source, keeping its value. No FSM, thread, queue-transport or scheduler
  logic changes.

### Deviation justifications

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| `-H` defaults become `None`, resolved when the command starts rather than as an import-time class attribute | At import, the rule runs for every `hs` command: an unset `host_env` would fail `hs list`, and a `host_eval` command runs at each start | Keeping the import-time default (one line each): a configuration error in `[server]` would break unrelated commands. Flagged for the plan gate (§5) |
| An `_env` form naming an unset variable raises `ConfigurationError` | The rule must decide "given" consistently; today such a value reaches the socket layer as `None` | Treating it as not given: silently dials another address, the class of failure this fix removes |

## 4. Rabbit holes (resolved)

- **Can "given" be told from the value `localhost`?** Yes, by layer. A variant form hides the key
  from a bare `which`, so the lookup goes by form first (research/01 F1–F2).
- **Where can a core helper live without a cycle?** In `core/config.py`. `cluster/` is out
  (research/01 F7).
- **Testing a default read at import.** Use child interpreters (`config_repr`, `file_layer`) for the
  readers, and constructed layered `Configuration`s for the function. Never patch `config` with a
  `Namespace` (research/02 F1–F2).
- **Does anything assert the old default?** No test does, and no doc shows a remote client relying
  on `bind`. The `security.rst` TLS example is fixed by this change (research/02, research/03 F1–F2).

## 5. Risks & open questions

- **For the plan gate: start-time resolution** (§3 deviation).
  - `ClientApp.host` and `SubmitApp.queue_server` read `None` until `check_args` and
    `check_arguments` run. An embedding caller that builds the app and reads `.host` without running
    it now sees `None`.
  - No code in the tree does this (research/02).
- **For the plan gate: the unset-`_env` refusal** is a new error path. It applies only to the
  command that dials, with exit status as for any configuration error.
- **`hs config get server.host` prints `localhost`** when nothing gives it, which may not be the
  address dialled. The `.host` entry states the rule, and the GOAL accepted this cost (Q1).
- **A file's `host = ""`** is dialled as given, the same as the GOAL's non-goal for an empty
  variable. No test pins it either way.
- **`hs cluster` ignores `advertise_env`/`advertise_eval`** (it reads `.get('advertise')`;
  research/01 F5). The client honours them, so the two differ only for those forms. This is outside
  R6. A follow-up seed is suggested.
- **Docs build and 3.11 gate need network for their venvs** (pypi) the first time. The sitemap
  extension needs the sandbox off.

## 6. Verification strategy

Tests come first (P1). Each test that needs the fix is a strict xfail with reason
`client-host-default P2`, and P2 removes them. New `tests/test_client_host_default.py`:

- **Units** (`@mark.unit`), on `client_host(layered(...))` under a guarded import
  (`getattr(config_module, 'client_host', None)`). Rows:
  - the seed;
  - `host` over `advertise` (different layers);
  - `advertise` over a specific `bind`;
  - a specific `bind`;
  - `bind` `0.0.0.0`, and `""`;
  - nothing given → `localhost`;
  - `host = "localhost"` with `advertise` and `bind` set → `localhost`;
  - a file's `advertise` `""` or `"<none>"` falling through to `bind`;
  - `host_env` (set), `host_eval`, `advertise_env`;
  - an unset `host_env` raises `ConfigurationError` naming `server.host_env`;
  - `host` from the `_` layer;
  - no row returns `'<none>'`;
  - `WILDCARD_BINDS` is importable from both modules and is the same object.
- **Readers** (`@mark.integration`). These are child interpreters with the configuration given by
  file (`file_layer`) or environment (`config_repr`), one child per case, cached.
  - Each case checks `hs client` (`check_args` then `.host`), `hs submit --queue`
    (`check_arguments` then `.queue_server`) and `QueueConfig.load().host`.
  - Cases: the seed by file and by environment, each R3 branch, `host` given as `localhost`, a file's
    empty or `<none>` `advertise`, and `host_env`.
  - Rows that pass on HEAD stay unmarked. The rest are xfail P2 (research/02 matrix).
- **`-H` wins** (R2). The configuration is given by file: `host`, `advertise` and a specific `bind`.
  `ClientApp.from_cmdline(['-H', 'x.example'])` and `SubmitApp.from_cmdline(['-q', '-H',
  'x.example', …])` both keep `x.example`.
- **API defaults** (R5), one child with the seed file and `HYPERSHELL_SERVER_ADVERTISE` set:
  - `client.DEFAULT_HOST`, `submit.DEFAULT_HOST`, `QueueConfig().host` and `server.DEFAULT_BIND` are
    all `localhost`;
  - the `address` default of `ClientThread.new` and `run_client` is `('localhost', …)`.
- **Server side** (R6): `ServerApp.from_cmdline([]).host` and `ClusterApp.from_cmdline(['t.in']).host`
  equal the configured `bind`.
- **Help and docs** (`@mark.unit`, xfail P2):
  - The `-H` entries of both `APP_HELP`s and of `client_help.rst` and `submit_help.rst` name
    `server.host` and `server.advertise`.
  - The reference's `.host`, `.advertise` and `.bind` (`parse_reference`) state the rule.
  - `docs/cli/client.rst` and `docs/cli/submit.rst` each carry a new `versionchanged:: 2.9.0`
    naming `-H`.
- **Drive** (P2). `hs client --help` and `hs submit --help` in a throwaway site, with
  `HYPERSHELL_CONFIG_FILE=` set empty so the developer's configuration cannot leak, show the rule.
- **Gate** (P3):
  - the whole suite serially under 3.14 and 3.11;
  - the Sphinx `-W` build;
  - `zsh -n` on the completion.

---

*Backing research: [`research/00-digest.md`](research/00-digest.md).*
