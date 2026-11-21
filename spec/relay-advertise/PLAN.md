# PLAN — `hs relay` names the address its site clients dial

> **Status:** Draft for review · **Last updated:** 2026-11-21
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). Lean plan (`appetite: small`, no fan-out):
> the GOAL names #107's design as the pattern, and the change touches no high-blast-radius file.
> Plan-time evidence: the drive [`drive/advertise_drive.sh`](drive/advertise_drive.sh) and the test
> prototype [`research/prototypes/relay_advertise_proto.py`](research/prototypes/relay_advertise_proto.py),
> both dry-run on `08d867df`.

## 1. Summary

The site cluster already knows the one address its clients dial: `AutoScalingCluster` computes
`advertised = (advertised_host(bind, advertise), port)` (#107) and takes `advertise=`. The relay
passes nothing there, and `zone_launch` recomputes the address from the bind alone. The change
threads one optional value, `advertise`, from `RelayApp` (option > `relay.advertise` > none) through
`RelayThread` into `site_cluster`, which gives it to the cluster; `zone_launch` then uses the
cluster's `advertised` address instead of recomputing it, so the first zone and every further zone,
fixed or following a topology, tell their clients the same address by construction. One declared
key (`relay.advertise`, text, empty unset), one option, their docs and completions. It mirrors
`ClusterApp.advertise_host` line for line, which is why the appetite holds.

## 2. Design

### 2.1 The launch: `relay/site.py` (R1, R3)

- **`site_cluster(..., advertise: Optional[str] = None)`** (new keyword, last): passes
  `advertise=advertise` to `AutoScalingCluster(...)`, so `cluster.advertised` is
  `(advertised_host(address[0], advertise), address[1])` and `cluster.launch` (replaced below) and
  any failure text agree. It never reads configuration.
- **`zone_launch`** builds `client_launch(DEFAULT_REMOTE_EXE, cluster.advertised, queue.auth, ...)`
  in place of `(advertised_host(queue.host), queue.port)`. Its signature is unchanged: the address,
  like the site key, is the cluster's. With `advertise=None` this is exactly develop's address
  (`advertised` is built from the same bind and port), so R3 holds with no branch. There is no
  per-zone address (non-goal): `zone_launch` cannot be given one.
- `advertised_host` is no longer called in `site.py`; drop it from the import. The module
  docstring (its "Site clients dial ..." sentence) and `site_cluster`'s docstring (the `address`
  paragraph, a new `advertise` sentence) state the rule: `advertise` when given, else the bind
  address, else this host's name for a wildcard bind.
- **Both fleet modes.** Every fleet's autoscaler is a `SiteScaler` built by `site_scaler(machine,
  launch, view)`, and it launches with `machine.launch` whether it follows a topology or keeps a fixed
  fleet (`test_every_launch_exposes_its_zone_and_is_fenced` pins `machine.launch is cluster.launch`).
  So one address in `cluster.launch` and in each `zone_launch(cluster, ...)` covers both modes.

### 2.2 The relay thread: `relay/__init__.py` `RelayThread` (R1)

- `RelayThread.__init__(..., advertise: Optional[str] = None)` (new keyword, last):
  `site_cluster(..., advertise=advertise)`. The further zones' `zone_launch(self.cluster, name, ...)`
  calls are unchanged; they take the address from the cluster. The constructor never reads
  configuration (#107's rule, invariants §11).

### 2.3 The application: `relay/__init__.py` `RelayApp` (R1, R2, R3, R4)

- **Option.** `advertise: Optional[str] = None` and `interface.add_argument('--advertise',
  default=None)`, declared right after `--bind`. Its default is `None`, not the key, so the option
  can be told from the key (as #107).
- **Resolution.** `@functools.cached_property advertise_host -> Optional[str]`: `self.advertise` if
  it is not `None`; else `config.relay.get('advertise', None)`, read as unset when `None`, `''` or
  `PARAM_UNSET`; else `None`. It never reads `server.advertise` (R3). Docstring: "Address site
  clients are told to dial: --advertise, else relay.advertise, else None (the bind rule)."
- **Refusal (R4).** In `check_args`, right after the `--no-confirm` refusal and before anything
  else: `if self.advertise is not None and not self.advertise.strip(): raise
  ArgumentError('Expected an address for --advertise (given an empty value)')` (#107's wording).
  `check_args` is the first call in `run`, before `bind_database` (re-exec), the lock, `ensuredb`
  and any upstream connection, so the relay exits 2 with no database file (dry-run: the prototype
  and the drive's `empty` mode). A blank key value is passed on as given (as #107; non-goal
  "checking the address"); an empty key is unset.
- **Wiring.** `run()` passes `advertise=self.advertise_host` to `RelayThread(...)`.
- **Logging.** No new line. The site launch command, `-H` included, is already logged at DEBUG
  (`LaunchActuator.describe`); an INFO line would add this host's name to every default relay's log
  for no new information. (Left to the plan by the GOAL; decided here.)
- **Help (R6).** `APP_USAGE`: `[--bind ADDR] [--advertise ADDR]`, rewrapped so no line exceeds the
  current longest; `APP_HELP` options: `      --advertise     ADDR  Address site clients dial instead
  (default: by --bind).` after `--bind`; and one description paragraph after the site-key paragraph:
  site clients dial the relay at its bind address, or this host's name when it binds every
  interface, unless `--advertise` (`[relay] advertise`) names another; under TLS with the system CA
  bundle and no `servername`, that name must be one the relay's site certificate carries.

### 2.4 The key: `core/config.py`, `core/config_schema.py` (R2, R3, R5)

- `default['relay']`: `'advertise': PARAM_UNSET,  # Address site clients dial (unset: the bind, or
  this host's name)` between `bind` and `port` (as `server.advertise`). `hs config get
  relay.advertise` then prints `<none>` instead of a not-found error.
- `KEYS`: `'relay.advertise': T` after `'relay.bind'`. `EMPTY_UNSET`: `'relay.advertise'` after
  `'relay.bind'`. `HYPERSHELL_RELAY_ADVERTISE` then maps to `('relay', 'advertise')` as text (today
  untyped), and an empty variable sets nothing (today `''`; dry-run).
- `docs/_include/config_param_ref.rst` gains the `[relay]` `.advertise` entry after `.bind` **in the
  same commit** (§10; `test_reference_entries_declared` and `test_empty_unset_keys` enforce it): "Address
  the *clients* at the site are told to dial (default: by ``.bind``)", its rule, that the
  ``--advertise`` option takes precedence, that ``server.advertise`` does not reach a relay, its
  environment form ``HYPERSHELL_RELAY_ADVERTISE``, the TLS sentence, and that it is about the address
  site clients dial, not the client bounds a zone advertises upstream. `(default: by ``.bind``)` is
  words, so `documented_default` reads none and the key is an optional text key, as required. The
  `[server]` `.advertise` entry's "and so do a relay's site clients" becomes a pointer: a relay's site
  clients are told ``relay.advertise`` instead.
- **Fence (R5): no code change.** `is_relay_variable` already maps `HYPERSHELL_RELAY_ADVERTISE`
  under `relay` (it does today, untyped), so `scrub_environment` removes it, and `site_config_copy`
  drops the whole `[relay]` table. The prototype's `test_relay_advertise_is_fenced` is a green guard.

### 2.5 Docs and completions (R6)

- `docs/_include/relay_usage.rst`: ``[--advertise ADDR]`` after ``[--bind ADDR]`` (rewrap the
  second line).
- `docs/_include/relay_help.rst`: the `--launcher` entry's address sentence states the new rule
  (bind address, host name for *0.0.0.0*, unless ``--advertise`` names another); a new
  ``--advertise`` *ADDR* entry between ``--bind`` and ``--port``: what it is, that it wins over
  ``relay.advertise``, `server.advertise` does not reach a relay, that it is passed on as given and
  must be reachable from the nodes, the TLS sentence, "See also ``relay.advertise``". It says plainly
  that this is the address site clients dial, not the client bounds a zone advertises upstream. The
  `--bind` entry gains "unless ``--advertise`` names another address".
- `docs/security.rst` (Peer Verification Modes, the advertised-address paragraph): widen to "A
  `cluster` tells the clients it launches, and a relay its site *clients*, which address to dial ...
  ``--advertise`` (or ``server.advertise``; for a relay, ``relay.advertise``)". The existing
  `versionadded:: 2.9.0` body widens to "``--advertise``, ``server.advertise`` and
  ``relay.advertise``." No new marker (GOAL clarification 3).
- `docs/_include/config_intro.rst` (optional-text-key list): add ``relay.advertise`` beside
  ``server.advertise``.
- `share/bash_completion.d/hs` `_hs_relay`: `--advertise` after `--bind` in `all_opts`; case arm
  `--advertise) COMPREPLY=($(compgen -A hostname -- "${current}")); return ;;` (as `_hs_cluster`).
  `share/zsh/site-functions/_hs` `_hs_relay`: `'--advertise[Address site clients dial (default: by
  --bind)]:address:_hosts' \` after the `--bind` line (no brackets inside the description).
- **Agent docs.** `AGENTS.md` (the `client_launch()` bullet, "the relay has no override") and
  `.agents/factory/invariants.md` §11 (the one-rule bullet) state the new rule: `--advertise` /
  `server.advertise` for remote `hs cluster` modes, `hs relay --advertise` / `relay.advertise` for a
  relay's site clients (never `server.advertise`), else the bind, else `HOSTNAME`; a relay's
  `zone_launch` takes the site cluster's `advertised`. Lockstep: same commit.
- `tests/test_cluster_advertise.py` module docstring ("with no override") → names the relay's
  own override.
- No man page (regenerated at release; GOAL "left for /hs-plan"). No `docs/cli/relay.rst` change.

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1   | `RelayApp --advertise` → `RelayThread(advertise=)` → `site_cluster(advertise=)` → `AutoScalingCluster.advertised`; `zone_launch` uses `cluster.advertised` for every zone; `SiteScaler` launches `machine.launch` in both modes |
| R2   | `RelayApp.advertise_host`: option, else `relay.advertise` (unset for `None`/`''`/`PARAM_UNSET`); `KEYS`/`EMPTY_UNSET`/`default` entries so the variable maps as text |
| R3   | `advertise=None` gives `advertised_host(bind)`, develop's address; `advertise_host` never reads `server.advertise`; empty key unset |
| R4   | `check_args` refuses an empty or blank `--advertise` first, `ArgumentError` → exit 2, before `bind_database` |
| R5   | `relay.advertise` declared text in `KEYS`, in `EMPTY_UNSET`; fenced by the existing matcher-based `is_relay_variable` and `site_config_copy` |
| R6   | `APP_USAGE`/`APP_HELP`, `relay_usage.rst`, `relay_help.rst`, `config_param_ref.rst` (`[relay]` and `[server]`), `security.rst`, `config_intro.rst`, bash/zsh completions, `AGENTS.md`, `invariants.md` §11 |

## 3. Invariant gate (AGENTS.md constitution check)

Gate #1 (pre-research): the change touches §9 (TLS: documentation only), §10 (a declared key and
its reference entry), §11 (the one-address rule, `zone_launch`, `fence_site`), §12 (docs,
completions, markers, tests). Intent sane against each; nothing in §1–§8.

Gate #2 (post-design):

- **§11 one rule for the advertised address** — still one rule, `advertised_host`, now fed the relay's
  own override; computed once per relay in the `AutoScalingCluster` constructor and reused by every
  `zone_launch`, so no launch site can diverge. Constructors take `advertise=` and never read
  configuration; only `RelayApp` reads `relay.advertise`. The invariant's text is updated (lockstep).
- **§11 relay launches** — still only through `client_launch()` via `zone_launch`, `expose_zone=True`,
  every launch checked by `fence_site`. The key never enters an argv (the address is not a secret).
- **§11 relay fleets never stop / never terminate** — untouched.
- **§10 configuration** — `relay.advertise` gets its kind in `KEYS` and its reference entry in the same
  commit; `EMPTY_UNSET` beside `relay.bind`; the relay variable is recognised by the matcher
  (`is_relay_variable`) and so fenced. `config` stays read once at import; `RelayApp` reads it at run.
- **§10 relay process** — `check_args` runs before `bind_database`'s re-exec; the re-executed relay is
  given the same argv (`RelayApp.argv`), `--advertise` included, and re-checks it.
- **§9 TLS** — no behaviour change; cert/key still not forwarded; docs state when the name matters.
- **§12** — help snippets, completions and prose in the cycle; no new version marker (the page and
  section carry `versionadded:: 2.9.0`); new tests tagged `@mark.unit`/`@mark.integration`; exit status
  via `ArgumentError` (cmdkit's `bad_argument`), no literal.

### Deviation justifications

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| The CLI option (P2) and its `docs/_include` help snippets and completions (P3) land in separate phase commits, against §12's "same commit". | The foreman's brief asks for a separate docs and completions phase, so each phase's verify stays narrow. | Folding P3 into P2 is the literal rule; the branch is squashed into one commit at `hs-publish`, so the merged history keeps the rule either way. The key's reference entry, which a test enforces per commit (§10), stays in P2. **Foreman's call** (see §5). |

## 4. Rabbit holes (resolved)

- *Does a further zone's fleet get the override?* → yes by construction: `zone_launch` takes
  `cluster.advertised`; a separate `advertise=` parameter on `zone_launch` was rejected (a second
  path a caller could forget, and it would invite a per-zone address, a non-goal).
- *Does the topology-following mode launch differently?* → no: `SiteScaler` launches `machine.launch`
  in both modes (existing test pins `machine.launch is cluster.launch`).
- *Is the fence already right?* → yes: `HYPERSHELL_RELAY_ADVERTISE` is already a relay variable by the
  matcher (dry-run: `is_relay_variable` true, scrubbed; the copy drops `[relay]`).
- *Does R4 fire before the database?* → yes: `check_args` precedes `bind_database`; dry-run of the
  drive's `empty` mode on develop exits 2 with no database file (argparse's refusal today; the new
  message after P2).
- *Can the drive run here?* → yes: upstream `hs server --forever --no-tls -H 127.0.0.1` and a relay
  `--bind 127.0.0.1 --no-tls` with keys from `HYPERSHELL_SERVER_AUTH` / `HYPERSHELL_RELAY_UPSTREAM_AUTH`;
  site clients need the venv's `bin` on `PATH` (`hyper-shell client`), which the drive sets. The
  upstream runs on the site database, not `--no-db`: an in-memory `hs server --forever` exited 0 as
  soon as its only relay drained (it has no scheduler for `--forever` to keep), so a second relay
  found nothing to dial. On develop the drive fails as the gap predicts (`told` at its `option` case,
  argparse's exit 2; `empty` with no refusal naming the option); with the design emulated in a
  scratch copy all three modes pass (`told` 22 s, `empty` 2 s, `healthy` 6 tasks in 9 s).

## 5. Risks & open questions

- **Foreman sign-off: the §12 phase split** (deviation table). Default if silent: keep the split.
- **Foreman sign-off: plan-time drive and prototype committed** under `spec/relay-advertise/`
  (`drive/advertise_drive.sh`, `research/prototypes/relay_advertise_proto.py`), as
  `cluster-launch-followups` did, so every `verify:` was dry-run. The builder may amend either; a
  changed check is noted in its commit.
- **Foreman sign-off: `hs config get relay.advertise` prints `<none>`** once the default exists
  (today a not-found error, exit 3), as `server.advertise` does.
- **Foreman sign-off: a docs venv of this cycle's own** (`/tmp/claude-501/docs-venv-rad`) in place of
  the brief's `/tmp/claude-501/docs-venv`. That shared venv lost its `pyvenv.cfg` and `sphinx-build`
  mid-session (another session rebuilding it), and `uv run` re-installs the project from whichever
  worktree ran last, so a shared docs venv can build another tree's autodoc. The command is otherwise
  the brief's.
- `localhost` in the drive resolves to `127.0.0.1` for an `AF_INET` dial; if a host resolves it only
  to `::1`, the `healthy` mode fails at the site clients, not the relay (the record-only `told` mode
  is unaffected).
- Docs-text tests (`*_documented_*`) check presence and order only, not wording.

## 6. Verification strategy

- **Tests first (P1):** `tests/test_relay_advertise.py` from the prototype (32 tests: 27 red on
  `08d867df`, 5 green guards), plus one entry each in `tests/test_config_schema.py` `MATCHES` (red:
  untyped today) and `tests/test_env_config.py` `EMPTY_NOT_GIVEN` (red: the empty variable is held
  as `''`). The 29 red ids are [`red.txt`](red.txt); dry-run: `red_check` 29 of 29 red on
  `08d867df`, `rest_green` 333 passed over the five test files.
- **Unit:** `site_cluster`/`zone_launch` address table (specific, wildcard, empty, override on both);
  `RelayThread` every fleet's `machine.launch`; `RelayApp.advertise_host` (option, key, option over
  key, empty and `None` key, `server.advertise` ignored); the blank-option refusal; help order; key
  declaration, matcher kind, empty variable; reference entry order and the `[server]` pointer; the fence.
- **Integration:** a relay process (`relays` fixture, bind 127.0.0.1, recording launcher): bind, option,
  two zones, key, option over key, empty key, `server.advertise` alone; `--advertise ''`/`' '` exit 2,
  no database.
- **Drive** (`temp_site.sh`): `told`, `empty`, `healthy` modes (GOAL evidence plan, one to one).
- **Gate:** the suite serially under 3.14 and 3.11, the Sphinx `-W` build, `hs relay --help`.
