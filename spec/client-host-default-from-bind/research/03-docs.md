# Research 03 — docs, markers, completions, release note

Read-only survey of the worktree at `1dd734d7` for R8. Line numbers are this worktree's.

## Findings

1. **The R8 surfaces are as GOAL names them; nothing else in the docs needs to change.**
   - `docs/_include/config_param_ref.rst` `[server]`: `.bind` :179-185, `.host` :187-193, `.advertise` :195-208
     (its `versionadded:: 2.9.0` `:pr:`107`` block :205-208).
   - `docs/_include/client_help.rst:56-57`, `docs/_include/submit_help.rst:54-57` (`-H`), and the matching
     help strings `src/hypershell/client.py:1625` and `src/hypershell/submit.py:1543`, both printing
     `{DEFAULT_HOST}` (built-in `localhost`), which is wrong on `develop` whenever configuration sets `bind`/`host`.
   - Grep of `docs/**/*.rst`, `README.rst`, `AGENTS.md`, `.agents/factory/invariants.md` for `server.host|bind|advertise`,
     `SERVER_HOST|BIND`, "dial", `hs client`, `submit --queue`: every other hit is about **launched** clients
     (`cluster_desc.rst:30-35`, `cluster_help.rst:60-80`, `security.rst:379-389`, `relay_help.rst:83`,
     `config_param_ref.rst:829-840` `relay.advertise`, AGENTS.md:476-483, invariants.md:274-283), the database
     `host` (`config_intro.rst:80`, `security.rst:504`), or the empty-value rule (`config_intro.rst:151-153`,
     which lists `server.bind`/`server.advertise` but not `server.host`; consistent with the GOAL non-goal on
     an empty `HYPERSHELL_SERVER_HOST`). None of those change: `advertise` keeps its meaning for launched clients.
   - **No page shows a client on another node relying on `bind`.** `getting_started.rst:146-161` passes
     `hs client --host '<HOSTNAME>'` explicitly. `security.rst:679-716` (TLS deployment, Steps 2-3) has the
     server file `bind = "0.0.0.0"` and a separate *client-side* file `[server] host = "hypershell.example.com"`:
     that example is broken on `develop` and every 2.8 (the client ignores `host`, dials `bind`'s default
     `localhost`) and becomes correct with this fix. No text change needed; worth one line in REVIEW.
2. **No test or tool checks help text against `docs/_include/*_help.rst`** for client or submit. There is no
   `tools/` dir and no generator; the `*_help.rst` files are hand-kept. `tests/test_main.py:73` only checks
   `hs client --help` prints `ClientApp.interface.help_text`. Relay cycles added their own doc-pinning tests
   (`tests/test_relay_advertise.py:230-240`); this cycle may add one for the `-H` entries (optional).
   `tests/test_config_schema.py::test_documented_default` (:372) parses `(default: …)` from reference entries;
   it asserts `server.bind == 'localhost'` and `server.advertise is None`, and nothing for `server.host`. A worded
   default (`by ``.advertise`` or ``.bind```) parses as `None` and passes. **Constraint for the plan:** keep
   `default['server']['host'] = 'localhost'` in `core/config.py:143`. If the build drops it to detect "given",
   `test_empty_unset_keys` (:384) would then classify `server.host` as an optional text key and demand it in
   `EMPTY_UNSET`, which GOAL rules out (non-goal: empty `HYPERSHELL_SERVER_HOST`). Detect "given" from the
   layers instead.
3. **Completions (`share/`): no change required** (no option added or renamed). One stale description:
   `share/zsh/site-functions/_hs:507` (`_hs_client`) says `-H … [Hostname for server (default: localhost)]`,
   already wrong on `develop`. Optional one-word fix: `(default: by server.host)`. `_hs:473` (`_hs_submit`,
   "used with --queue") and bash (`share/bash_completion.d/hs`, no descriptions) need nothing. Man pages
   (`share/man/man1/*.1`, `-H` at `hs.1:1062-1063`, `:1545-1546`) are rebuilt only by `hs-release`; do not touch.
4. **AGENTS.md / invariants.md (optional, code-agent docs).** Neither states where an un-launched client
   dials. Consider one bullet beside the `advertised_host` rule (AGENTS.md:474-483, invariants.md:274-283)
   naming the new shared helper, so a later cycle does not reintroduce a `bind` default. Not R8.

## Doc changes

| File:line | Current | Proposed |
|---|---|---|
| `config_param_ref.rst:179-185` `.bind` | "…bind the server to *0.0.0.0*. A `cluster` tells the clients it launches to dial the bind address … unless ``.advertise`` names another." | Keep; append: "A `client`, or ``submit --queue``, given no ``-H`` dials the bind address only when neither ``.host`` nor ``.advertise`` is given and it is one specific address; otherwise *localhost*." + marker (below). |
| `config_param_ref.rst:187-193` `.host` | "Host address that clients connect to (default: `localhost`). Counterpart to ``.bind`` … ``.host`` is the address that `client` instances and queue-direct ``submit --queue`` dial to reach the server. Set this to the server's reachable hostname or address when running clients on other machines." | "Address that *clients* dial (default: by ``.advertise`` or ``.bind``). / Counterpart to ``.bind``: where ``.bind`` is the local address the server listens on, ``.host`` is the address that `client` instances and queue-direct ``submit --queue`` dial when given no ``-H``. Set it to the server's reachable name when clients run on other machines, so one shared file serves both, e.g. ``bind = "0.0.0.0"`` with ``host = "login00.example.edu"``. When no configuration file or variable gives it, they dial ``.advertise`` if given, else ``.bind`` when it is one specific address, else *localhost*. The ``-H`` option takes precedence. Its environment form is ``HYPERSHELL_SERVER_HOST``." + marker. |
| `config_param_ref.rst:195-204` `.advertise` | "Address the clients a `cluster` launches are told to dial (default: by ``.bind``). Overrides the rule under ``.bind`` … its site *clients* are told ``relay.advertise`` instead. Its environment form is …" | Keep; add before the environment sentence: "A `client` or ``submit --queue`` given no ``-H`` also dials it when ``.host`` is not given." + second marker in the existing `.. only:: html` block. |
| `client_help.rst:56-57` | "``-H``, ``--host`` *ADDR* / Hostname or IP address to connect with server (default: localhost)." | "Hostname or IP address to connect with server (default: ``server.host``). / With no ``-H``, the client dials ``server.host`` when the configuration gives it, else ``server.advertise``, else ``server.bind`` when it is one specific address, else *localhost*." |
| `submit_help.rst:54-57` | "Hostname or IP address for server (default: localhost). / Only used with ``--queue`` mode to specify the server to connect to." | "Hostname or IP address for server (default: ``server.host``). / Only used with ``--queue`` mode to specify the server to connect to. With no ``-H``, it is ``server.host`` when the configuration gives it, else ``server.advertise``, else ``server.bind`` when it is one specific address, else *localhost*, as for `client`." |
| `client.py:1625` | `-H, --host          ADDR  Hostname for server (default: {DEFAULT_HOST}).` | `-H, --host          ADDR  Hostname for server (default: server.host, else server.advertise,` / `                          else server.bind if one address, else localhost).` (continuation indented to the description column, as `-b` at :1621-1622) |
| `submit.py:1543` | `-H, --host         ADDR    Hostname for server (default: {DEFAULT_HOST}). Used with --queue.` | `-H, --host         ADDR    Hostname for server, used with --queue (default: server.host, else` / `                           server.advertise, else server.bind if one address, else localhost).` |
| `_hs:507` (optional) | `[Hostname for server (default: localhost)]` | `[Hostname for server (default: by server.host)]` |

Help strings name keys, not a resolved value, so they match the static `*_help.rst`. If the plan prefers
showing the resolved address (as `-w` does, `(default: client.bundlewait, {DEFAULT_BUNDLEWAIT})`), the
`_help.rst` must still give the rule in words.

## Marker placement

AGENTS.md § Docs (:648-672): `versionchanged:: X.Y.0` with one short clause, at the narrowest scope;
"Generated help snippets are never hand-marked: the marker goes beside the `.. include::`"; "A marker written
during a cycle has no PR number yet; `hs-release` adds it" — so **no `:pr:`** now (relay `--remote-exe`,
`eac8615c`, wrote its `docs/cli/relay.rst` marker with none).

- **`client_help.rst`** is included by `docs/cli/client.rst:49` and `docs/manual.rst:101`; **`submit_help.rst`**
  by `docs/cli/submit.rst:34` and `docs/manual.rst:125`. Precedent: `eac8615c` marked `docs/cli/relay.rst`
  only; `manual.rst` carries only the `:pr:`104`` markers. Put each new marker in the `docs/cli/` page, as
  the last of the existing marker run, directly above the `.. include:: ../_include/*_help.rst` (that is the
  local convention in `client.rst:18-49` and `submit.rst:18-34`; `relay.rst` puts them after):
  - `docs/cli/client.rst` (before :49):
    ```
    .. versionchanged:: 2.9.0
       With no ``-H``/``--host``, the client dials ``server.host``, else ``server.advertise``, else a
       specific ``server.bind``, else *localhost*; before, it dialled ``server.bind``.
    ```
  - `docs/cli/submit.rst` (before :34):
    ```
    .. versionchanged:: 2.9.0
       With ``--queue`` and no ``-H``, it dials ``server.advertise`` or a specific ``server.bind`` when
       ``server.host`` is not given, as `client` does; before, *localhost*.
    ```
- **`config_param_ref.rst`**: inside the entry, in a `.. only:: html` block at the end of the entry body
  (precedent `:286-295` `.attempts`, two `versionchanged` in one block; `:249-252` `.bundlesize`):
  - `.bind`: new block — `.. versionchanged:: 2.9.0` / "Clients given neither ``.host`` nor ``.advertise``
    dial it only when it is one specific address, else *localhost*; ``submit --queue`` now does too."
  - `.host`: new block — "``hs client`` dials it; before, it dialled ``.bind``." (`versionchanged`, not
    `versionadded`: the key is in 2.8's defaults, though 2.8's reference omits the entry.)
  - `.advertise`: append to the existing block after `versionadded` `:pr:`107`` — "``hs client`` and
    ``submit --queue`` dial it when ``.host`` is not given."

## Release note

Keep `spec/client-host-default-from-bind/RELEASE-NOTE.md`: both behaviour-change cycles in `spec/` have one
(`relay-client-exe-deprecated-name`, `submit-input-checks`), planned as a PLAN/TECH item with a verify grep
(`relay-client-exe-deprecated-name/TECH.md:75`), and `hs-publish` (SKILL.md:84-88) wants the squash body as
plain text, top-level commands only, from which `hs-release` drafts notes. Format: a heading, one context
paragraph, `---`, then the body. Register: plain text, no backticks, "Earlier releases…", "Behaviour change:"
paragraphs. Draft body:

> hs client started without -H now dials server.host when a configuration file or HYPERSHELL_SERVER_HOST gives it, so one shared configuration serves a server bound to 0.0.0.0 and clients on other nodes. Earlier releases dialled server.bind, so such a client dialled its own node and was refused. Without host, a client dials server.advertise when given, then server.bind when it is one specific address, then localhost. hs submit --queue and QueueConfig.load() follow the same rule. -H still wins, and the Python API defaults stay localhost. server.advertise keeps its meaning for the clients hs cluster launches; a relay's site clients still use relay.advertise.
>
> Behaviour change: a configuration that gives server.host now has hs client dial it, not server.bind. One that gives server.advertise but not host now has hs client, hs submit --queue and QueueConfig.load() dial advertise. One that gives only a specific server.bind now has hs submit --queue and QueueConfig.load() dial it, where they dialled localhost; hs client is unchanged there. With 0.0.0.0 bound and neither key given, hs client dials localhost, not 0.0.0.0 (the same host).

## Verify

Docs: `uv run --python 3.12 --group docs sphinx-build -W --keep-going -q -E -b html docs <tmpdir>`
(the AGENTS.md command; TECH precedent uses a `$TMPDIR` output dir and `timeout 900`). Plus
`zsh -n share/zsh/site-functions/_hs` if `_hs:507` is touched, and greps: `! grep -q 'DEFAULT_HOST}' `
on the two help blocks, `grep -q 'server.advertise' docs/_include/client_help.rst docs/_include/submit_help.rst`,
`grep -c 'versionchanged:: 2.9.0' docs/cli/client.rst docs/cli/submit.rst` up by one each.

## Open risks

- **`hs config get server.host` prints `localhost`** (built-in default) even when the client dials
  `advertise`/`bind`. Docs say "when the configuration gives it"; consider one clause in `.host` saying the
  printed built-in value is not "given". GOAL Q1 accepts this cost.
- **Built-in `server.host` must stay** in the defaults table (finding 2), or `test_empty_unset_keys` fails.
- **Help-string drift:** no test pins `-H` text to `*_help.rst`; a small doc-pinning test like
  `test_relay_advertise_documented_in_the_help_pages` would hold it.
- **`.bind` marker wording** describes a narrowing for `hs client` (wildcard bind now dials localhost, which
  was the same host) and a widening for `submit --queue`; keep it one clause or split the client part to
  the `.host` marker if the build judges it too dense.
- **TLS:** a client now dialling `advertise` checks the certificate against that name (system-CA case);
  this is the intended use. `security.rst:379-384` speaks only of launched clients; an optional sentence
  there ("a client started by hand dials by the same `.host` rule") is out of R8's list.
