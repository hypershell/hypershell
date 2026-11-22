# 00 — Research digest: config-set-and-unread

Five briefs, read-only, on `develop` `d9901cf0` (the worktree's `src`). The fan-out ran despite
`appetite: small` under `hs-plan`'s high-blast-radius exception: R5 and R7 edit `server.py`, R7 edits
`cluster/remote.py` and `cluster/ssh.py`, and R8 edits `client.py`. Each decision below resolves the
briefs into one recommendation; [`PLAN.md`](../PLAN.md) builds on them.

| Brief | Topic |
|---|---|
| [`01-config-set-typing.md`](01-config-set-typing.md) | R1–R4: a path's kind, the refusal, writing, empty values, scopes |
| [`02-eager-attempts.md`](02-eager-attempts.md) | R5, R6: why `--eager` replaces the key; `-r` under `hs cluster`; every mode |
| [`03-cluster-evict.md`](03-cluster-evict.md) | R7: `hs server`'s refusal, the floor, the plumbing per mode |
| [`04-ratelimit.md`](04-ratelimit.md) | R8: `-R`'s type, the launch word, the 2.9.0a4 contract, #115 overlap |
| [`05-docs-conflicts.md`](05-docs-conflicts.md) | R9 docs map; overlaps with #115 and #116 |

## Decisions

**D1 — A path's kind: `match_path(path) -> Kind` in `core/config_schema.py`** (01 §1), placed after
`match_name`, matched as written: an exact key; a documented key plus `_env`/`_eval` (text); an
`[export]` entry or an `ssh.nodelist` group (text); a `relay.zone.<name>.<key>` (`ZONE_KEYS`); everything
else untyped. Feeding `match_name` a synthesized variable name is rejected: it folds case (so
`Server.eager` would turn boolean, against the Non-goals), lower-cases entries and confuses `_` with `.`.
The value is then read by the same `read_value` the environment layer uses, so the two agree by
construction.

**D2 — The refusal is a `cmdkit.cli.ArgumentError` raised in `ConfigSetApp.run` before `update()`**
(01 §2–3): exit 2 with the message alone (cmdkit's `main` catches it before the exception map), text
`<path>: expected <form>, given <value!r>` (the environment's message without `(from: …)`). `update()`
writes its backup first, so converting before it leaves file, backups and `Logs/` untouched. The
`type=smart_coerce` goes; `smart_coerce` stays in `core/types.py` (other readers).

**D3 — An empty `logging.file` is refused in `read_value`** (01 §3): `text != ''` on the path branch.
The environment never reaches it (`logging.file` is in `EMPTY_UNSET`), so its behaviour is unchanged.

**D4 — `--eager` takes the key as its default** (02 Q1): `default=eager_mode` on the `store_true`
option in both apps. **The value is read by the boolean words** at each app's start check
(`configured_eager` in `server.py`, called from `ServerApp.check_args` and
`ClusterApp.check_arguments`), and anything else is refused with exit 3 naming the key and its source
(02 option C). Truthiness (option A) would turn a 2.8 user's `eager = "no"`, written by today's
`hs config set`, into eager mode on upgrade. *Foreman sign-off* (it adds a start refusal for an
unreadable `server.eager`, one clause under release-note change 4).

**D5 — `hs cluster` defaults `-r` to `server.attempts - 1`** (02 Q2), as `hs server`. Help text
unchanged (both print the built-in default by convention). Every mode receives `max_retries`/`eager`
through `ClusterApp.run`'s one call (02 Q3); invariant §3's `attempts == max_retries + 1` is untouched.

**D6 — `hs cluster` reads `server.evict` at its start check and refuses there** (03 §2): a shared
`check_evict()` with `EVICT_MINIMUM = 10` in `server.py`, used by `HeartMonitor`, `ServerApp.check_args`
and `ClusterApp.check_arguments` (before TLS, the database and the port probe). The period reaches the
`ServerThread` through `ClusterApp.run`'s call and one new defaulted `evict_after` on `LocalCluster`,
`RemoteCluster` and `SSHCluster` (`AutoScalingCluster` already has it) (03 §3). `check_evict` also refuses
a non-finite period (a TOML `inf` crashes `HeartMonitor` with an `OverflowError` traceback today, 03 §1).
*Foreman sign-off* on that clause (it changes `hs server`'s `inf` from a traceback to a clean exit 5).

**D7 — `-R` takes a finite number on both commands** (04 §1): `parse_rate` in `core/types.py` over
`read_number` (plain `float` would take `inf`/`nan`); a whole word stays an `int`; negatives accepted as
today. `hs cluster`'s default becomes `config.client.ratelimit or None` (04 §3); `task.ratelimit` is no
longer read.

**D8 — A launched client gets a whole rate as an integer word** (04 §2): `plain_rate` beside
`plain_seconds` in `cluster/launch.py`. A fractional rate is emitted as a plain decimal, which a 2.9.0a4
client refuses: an invariant §11 bend recorded in PLAN's deviation table, *foreman sign-off*, and a
release-note clause ("a fractional rate needs clients of 2.9.0 or later").

**D9 — Docs** (05 Part A): the `hs config set` page points at the guide's typing rule through a new
label before `config_intro.rst:142`; the reference entries `.attempts`, `.eager`, `.evict`,
`client.ratelimit` name `hs cluster` with `versionchanged:: 2.9.0` in the house `.. only:: html` block;
the `-R` help in both apps and their snippets; markers on `docs/cli/{client,cluster,server,config_set}.rst`.
No `share/` completion or man page changes.

**D10 — Overlaps with #115 and #116** (05 Part B, 04 §4): clean if the build leaves `client.py:1312-1314`,
`cluster/__init__.py:86-88`, `server.py:1213-1215`, `config_param_ref.rst:300-302`,
`config_intro.rst:146-153` and the `MATCHES` table in `tests/test_config_schema.py` alone, and puts the
new markers on `docs/cli/{client,cluster,server}.rst` above the ones #115 adds before the help include.

**Out of scope, found** (candidate seeds, not taken): the cluster classes' `max_retries` defaults
disagree with `ServerThread`'s (02); a text `attempts = "3"` in a file crashes every command at import
(02); a negative rate is "no limit" and passes unchecked (04); integers beyond 64 bits are written to TOML
by `hs config set` and the environment alike (01).
