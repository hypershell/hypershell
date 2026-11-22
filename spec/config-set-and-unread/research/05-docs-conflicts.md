# 05 — Docs map (R9) and in-flight conflicts with #115 / #116

Read-only research for `hs-plan`. Line numbers are `develop` `d9901cf0` (#116's merge base is
`08d867df`, one roadmap-only commit earlier; no `src/`, `docs/`, `share/` or `tests/` drift, so its
hunk numbers hold against `d9901cf0`). Merge behaviour checked with `git merge-file`: one unchanged
line between two edits merges clean; edits on touching lines, or inserts at the same point, conflict.

## Part A — the docs map

**`hs config set` page**
- App help `src/hypershell/config.py:265` `VALUE                   Value to be set.` mirrored by
  `docs/_include/config_set_help.rst:7-8` (`VALUE` / `Value to be set.`). Change both together.
- `docs/_include/config_set_desc.rst:1` `Set configuration option.` (one line; matches
  `SET_USAGE`, `config.py:257`). Desc snippets may carry prose and `:ref:` (precedent:
  `server_desc.rst:19`, `relay_desc.rst`), so the pointer to the rule can go here.
- Prose page `docs/cli/config_set.rst` (label `cli_config_set`): usage include :9, desc include :14,
  `|` :16, help include :18. It has no markers yet; the house place is between `|` (:16) and the help
  include (:18), as on `cli/cluster.rst`. The same three snippets are also included by
  `docs/manual.rst:211-215` (man build), so a `:ref:` in the desc must resolve there too (precedent:
  `config_param_ref.rst` uses `:ref:` and is included in `manual.rst:259`).
- **Guide's rule:** `docs/_include/config_intro.rst:142` `The value is read by the key's type:`
  (bullets :144-164). **No label of its own**; the nearest is `.. _config_env:` at :96 (section
  "Environment Variables", :98). Either point at `config_env`, or add a label (e.g.
  `.. _config_env_types:`) before :142. A label on a paragraph has no title, so every `:ref:` to it
  must give explicit text (`:ref:`by the key's type <config_env_types>``) or the `-W` build fails.
  `config_intro.rst` is included only by `docs/config.rst:8` (the manual uses
  `config_intro_alt.rst`), so a new label is not duplicated.

**Parameter reference** (`docs/_include/config_param_ref.rst`, included by `config.rst:94` and
`manual.rst:259`)
- `.attempts` :265-275 — names no command; "Counterpart to the ``-r``/``--max-retries``
  command-line option" (:272). No marker.
- `.eager` :277-282 — names no command ("If ``.attempts`` is greater than one, ..."). No marker.
- `.evict` :302-322 — :313-314 "``hs server`` and ``hs relay`` stop before they start if the value
  is not a duration." Two markers already in one `.. only:: html` block (:316-322); append a third
  there. The 10-second floor is not stated in the entry (it lives in `HeartMonitor`,
  `server.py:797-801`, a `RuntimeError`).
- `[client] .ratelimit` :461-467 — names no command; "See also ``-R``/``--ratelimit`` command-line
  option." (:467). Default text "no limit"; says ``0`` disables.
- Command naming in this file is mixed: `` `cluster` `` (:183, :198) and ``` ``hs server`` ``` (:313).
- House marker pattern (e.g. :248-251, :316-322):

  ```rst
          .. only:: html

              .. versionchanged:: 2.9.0
                 One short clause.
  ```
  (8 spaces, then 12, body at 15; several markers stack inside one `only` block.)
- `tests/test_config_schema.py` parses entries with `ENTRY` (:35):
  `^(?: {4})*``(\[[a-z_]+\]|\.[a-z_]+|\*)``\s*$`. Body edits are safe; **never put a body line
  holding only a ```` ``.name`` ```` term** — it would parse as a new entry and fail
  `test_reference_entries_declared`. No new key, so KEYS parity is untouched.

**`-R`, `-r`, `--eager` help**
- `src/hypershell/client.py:1574` `-R, --ratelimit     NUM   Maximum allowed tasks per second (default: none).`;
  option `:1616` `ratelimit: Optional[int] = config.client.ratelimit or None`, `:1619` `type=int`.
  Mirror `docs/_include/client_help.rst:155-157` (`*NUM*`, "Maximum allowed tasks per second per
  client. (default: none). Individual tasks are throttled if exceeding this average limit.").
- `src/hypershell/cluster/__init__.py:114` `-R, --ratelimit      NUM      Maximum allowed tasks per second per client (default: none).`;
  option `:240-241` (`config.task.get('ratelimit', None)`, `type=int`). Mirror
  `docs/_include/cluster_help.rst:358-360` (same text as client's).
- Usage lines show `[-R NUM]` (`client_usage.rst:4`, `cluster_usage.rst:5`): NUM fits a number.
- `-r` help prints the built-in `{DEFAULT_ATTEMPTS - 1}` (`server.py:1736`, `cluster/__init__.py:89`);
  rst `server_help.rst:63-64`, `cluster_help.rst:128-129` say `(default: 0)`. Same as `hs server`
  today, so R6 needs no help change. `--eager` rst (`server_help.rst:73-76`, `cluster_help.rst:138-141`)
  mention no key; optional.
- Other `int` types on the rate path: `client.py:1237`, `:1290`, `:1405`, `:1477`;
  `cluster/launch.py:193`, `:505` (passes `str(ratelimit)`, `:526-527`).
- **No test keeps `*_help.rst` equal to the app help strings.** Only `tests/test_config_schema.py`
  reads a docs file (the reference). `tests/test_key_option.py` sweeps `docs/`, `share/`, `src/`,
  `spec/*/drive/*` for a standalone `-k`/`--auth`; `server.auth` examples are fine.

**`share/` completions and man pages**
- zsh `_hs:523` (client) and `:629` (cluster) `-R ... :rate:` — free text, no type: **no edit
  needed**. zsh `-r` `(default: 0)` at :570, :600 — built-in default, unchanged. bash lists `-R`
  (:587, :693) with no value completion: no edit.
- `hs config set` value completions (bash :164-205, zsh :254-285) offer words only for
  `logging.level/style`, `database.provider`, `server.bind/auth`, `autoscale.*`, `relay.min/max/init`,
  `console.theme`; nothing about typing. No edit needed. (bash's `server.auth` suggestion is 16
  upper-case hex chars; now kept as text even if all digits.)
- `share/man/man1/*.1` are generated from `docs/manual.rst` and rebuilt only at release
  (`[release] ... rebuild man pages`, 2.9.0a6-a8): **do not edit in the cycle**.
- `tests/test_completions.py` lints `_hs` brackets only; untouched.

**CLI prose pages — marker pattern**
`docs/cli/{client,server,cluster}.rst`: after the desc include and `|`, a list of top-level
markers, then `.. include:: ../_include/*_help.rst` (client :35, server :43, cluster :72):

```rst
.. versionchanged:: 2.9.0
   One or two lines naming the option or key and the change.
```
(blank line between markers; some carry `:pr:`NN``; this cycle's get their PR at `hs-release`).
`docs/cli/config.rst` has no markers. The release note itself goes in the PR squash body, not a
docs file (AGENTS.md § Docs).

## Part B — overlap with #115 (`fix/relay-shutdown-tail`) and #116 (`feature/relay-advertise`)

| File | PR hunks (old lines) | Our likely edits | Verdict |
|---|---|---|---|
| `src/hypershell/client.py` | #115: 159-456 (scheduler/collector), 1305 ins, 1308, 1311, 1556 (`-w` help) | 1290, 1574, 1616-1619 | Clean. **1312-1314** (`if ratelimit is not None` / debug log) touch #115's 1311: leave them, or expect a conflict. |
| `src/hypershell/cluster/__init__.py` | #115: 87 (`-w` help) | 89-90 (only if `-r`/`--eager` help changes), 114, 162-165, 240-241, check_args, run (~294) | Clean (line 88 separates). Do not edit 86-88. |
| `src/hypershell/server.py` | #115: 148-352, 607-757, **1214** | 1768-1772, ~1913 | Clean. If R7 plumbs via `ServerThread`, it already takes `evict_after` (:1177, :1215); do **not** edit 1213-1215. |
| `src/hypershell/core/config_schema.py` | #116: 99-101 (KEYS, adds `relay.advertise`), 156 (EMPTY_UNSET) | `__all__` 30-32; new function after `match_name` (271-308) or at EOF | Clean. Do not reflow KEYS. Lookup tests driven by `KEYS` will see `relay.advertise` after #116 merges (text). |
| `tests/test_config_schema.py` | #116: inserts after 229 (`MATCHES`) | new tests | Put them at EOF or a new file, not beside `MATCHES`. |
| `docs/_include/client_help.rst` | #115: 44 | 155-157 | Clean. |
| `docs/_include/cluster_help.rst` | #115: ins after 111, 126 (`-Q` body) | 358-360; optional 138-141 | Clean. Avoid 127 (touches 126); 128-129 clean but one line away. |
| `docs/_include/server_help.rst` | #115: 61 | none required; optional 73-76 | Clean. Avoid 62. |
| `docs/_include/config_param_ref.rst` | #115: 290, ins after 301 (before `.evict`), 417/ins 423, 768/ins 777. #116: 202-203, ins after 752 | 272-275, 277-283, 313-314, ins after 322, 461-467 | Clean. Do not edit 300-302 (the `.evict` term line abuts #115's insert). |
| `docs/_include/config_intro.rst` | #116: 149-152 | label before 142 (optional) | Clean (143-148 separate). Do not touch the bullet at 146-153. |
| `docs/cli/cluster.rst` | #115: ins after 71 | new marker(s) | **Conflict if appended before the include.** Insert after :64 (before the `--no-db` marker) instead; or accept and keep both blocks. |
| `docs/cli/server.rst` | #115: ins after 42 | `server.eager` marker | Same: insert after :33 (before the stream marker); or keep both. |
| `docs/cli/client.rst` | #115: ins after 34 | `-R` marker | Same: insert after :28; or keep both. |
| `ROADMAP.md` | #115: 380, 635-636; #116: 358 | 353, 471 (seed status) | Clean (358 is 4 lines from 353). |

`AGENTS.md` and `.agents/factory/invariants.md` change in both PRs; this cycle should not need them.
`cluster/launch.py`, `local.py`, `remote.py`, `ssh.py`, `core/config.py` (#116 inserts at 210, the
relay table) are untouched by our likely edits or far from them.
