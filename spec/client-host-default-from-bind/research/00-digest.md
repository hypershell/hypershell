# Research digest — client-host-default-from-bind

Three read-only briefs, fanned out because the fix touches high-blast-radius files (`client.py`,
`core/queue.py`, `server.py`). Each decision below picks one answer where the briefs differ or
leave a choice open.

- [`01-config-rule.md`](01-config-rule.md): how to detect "given in any layer", where the helper
  lives, and the edge cases.
- [`02-tests.md`](02-tests.md): the test harness, which tests change, the xfail precedent and the
  verify commands.
- [`03-docs.md`](03-docs.md): the doc surfaces, where markers go, completions and the release note.

## Decisions

1. **Where the helper lives.** `client_host(base: Configuration = None) -> str` goes in
   `core/config.py`, which `core/queue.py` already imports. Putting it under `cluster/` would create an
   import cycle (`cluster/launch` → `client` → `core/queue`; 01 F7). `WILDCARD_BINDS` moves there too.
   `cluster/launch.py` imports it back and keeps it in its `__all__`, so
   `tests/test_cluster_advertise.py` and `advertised_host` stay as they are.

2. **"Given in any layer" is decided by the form, then the layer.** A bare
   `config.which('server', 'host')` raises `KeyError` when a layer gives `host_env` or `host_eval`,
   because the variant displaces `host` in the merged view (01 F2, 02 F3, both probed). The helper
   therefore finds the form the merged `[server]` table holds (`host`, `host_env` or `host_eval`,
   in the order `config_seconds` uses, `core/config.py:357`). It calls `which` on that form and
   treats `'default'` or `KeyError` as not given. It reads the value by attribute, so `_env` and
   `_eval` resolve. An `_env` form that names an unset variable raises `ConfigurationError`, naming
   the key and where it came from, as `config_seconds` and `resolve_auth` do.

3. **`<none>` and empty values are never dialled.** `PARAM_UNSET` counts as not given for all three
   keys, because a file can write it literally (01 F4). An empty `advertise` from a file counts as
   not given, as `ClusterApp.advertise_host` treats it; an empty variable already sets nothing,
   since the key is in `EMPTY_UNSET`. An empty `bind` is a wildcard bind (`WILDCARD_BINDS`). An
   empty `host` is held as given, per the GOAL's non-goal. The last fallback is the literal
   `'localhost'`.

4. **The rule is applied when the command starts, not at import.** The `-H` defaults of `hs client`
   (`ClientApp.host`) and `hs submit --queue` (`SubmitApp.queue_server`) become `None`, and each
   command resolves them when it checks its arguments (`ClientApp.check_args`,
   `SubmitApp.check_arguments` with `--queue`). Today both are class attributes evaluated at import
   for every `hs` command (01 F8). There the helper's `ConfigurationError` would break even
   `hs list`, and `submit.py` already runs a `host_eval` command at every start. `check_args`
   already reads `client.heartrate` at start, so start-time reading has precedent.
   `QueueConfig.load()` calls the helper directly.

5. **The Python API defaults keep `localhost` and stop naming `bind`** (Q2 (a)). The `QueueConfig.host`
   field default changes from `default.server.bind` to `default.server.host`, which is also
   `'localhost'`. `hs server`'s `DEFAULT_BIND` (`server.py:1142`) reads `QueueConfig.host` today.
   It changes to `default.server.bind`, so the server keeps its own built-in bind default (R5, R6).

6. **The built-in `server.host = 'localhost'` stays.** If it were dropped, `test_empty_unset_keys`
   would require `server.host` in `EMPTY_UNSET`, which the GOAL rules out (02, 03 F2).

7. **Tests.**
   - New `tests/test_client_host_default.py`. Unit rows call `client_host` on a real layered
     `Configuration`, built like `layered()` in `tests/test_client_bundle_keys.py:31`, never on a
     patched `Namespace`.
   - Child-process rows use `config_repr` and `file_layer` from `tests/test_env_config.py:84` and
     `tests/test_config_unread.py:36`. They cover `hs client`, `hs submit --queue` and
     `QueueConfig.load()`, the API defaults and the server side.
   - Where the fix is missing, a row is a strict xfail with reason `client-host-default P2`.
   - No existing test asserts the old behaviour (02).

8. **Docs and agent docs.**
   - The CLI help, the `*_help.rst` files, the zsh completion description, the `[server]` reference
     entries and the `versionchanged:: 2.9.0` markers all land in the same commit as the code
     (AGENTS.md § Docs; invariants §12).
   - Markers go in `docs/cli/client.rst` and `docs/cli/submit.rst` beside the `.. include::`, and in
     `.. only:: html` blocks inside the three reference entries (03).
   - AGENTS.md and `invariants.md` §11 say "only `ClusterApp`/`RelayApp` read" `advertise`. That
     stops being true, so both gain the client rule in the same commit.

9. **Release note.** `spec/client-host-default-from-bind/RELEASE-NOTE.md` is the squash body
   `hs-publish` uses. It follows the precedent and the draft in 03.

## Left as risks (PLAN §5)

- `hs config get server.host` prints `localhost` when nothing gives it.
- A file's `host = ""` is dialled as given.
- `hs cluster`'s `advertise_host` ignores `advertise_env` and `advertise_eval` (01 F5). This is a
  latent cluster bug outside this fix, and could be filed as a seed.
