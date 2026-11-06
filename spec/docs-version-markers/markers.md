# Marker inventory — docs-version-markers (R1)

Every `versionadded` / `versionchanged` marker for `2.9.0` under `docs/`, one row per marker.
[`check_markers.py`](check_markers.py) holds this table and the docs to each other: every row must
be present in its file with that directive, `2.9.0` and exactly those links, and every 2.9.0 marker
in `docs/` must have a row.

**Audit inputs.** `git diff 2.8.5 HEAD -- docs` (the deleted Tutorial, blog and roadmap pages
aside), and the GitHub release bodies for 2.9.0a1–a6, read against the 2.8.3, 2.8.4 and 2.8.5
release bodies. The baseline is 2.8.5, so behaviour backported to 2.8.4 or 2.8.5 is not 2.9 and is
not marked (see the list below the table). PRs come from the release notes, or from the
`(#NN)` subject of the first-parent merge on `develop`.

**Links.** `#NN` is a `:pr:` link. A seven-character hash is a `:commit:` link, used for the four
2.9 changes that reached `develop` as direct commits with no pull request (`--all` and the result
guard on `hs list`, colorized listings, signal mapping, container images). `docs/conf.py` defines
both roles.

**Scope.** Section-level under the heading where the whole section is new; beside the help
`.. include::` on CLI pages (the generated `_include/*_help.rst` / `*_usage.rst` are never edited,
and the `*_desc.rst` includes also feed the man page, so their changes are listed in the same page
marker); after the `autofunction` / `autoclass` on API pages, or under the section heading when
every entry in it changed. `_include/config_param_ref.rst` is also included by `manual.rst`, so its
markers sit inside `.. only:: html` and stay out of the man page. `_include/templates.rst`,
`_include/database.rst` and `_include/config_task_env.rst` are HTML-only (the man page uses the
`*_alt.rst` variants) and are marked in place.

| File | Scope | Directive | Links | First pre-release |
|------|-------|-----------|-------|-------------------|
| docs/cli/relay.rst | page, under the title | versionadded | #82 | 2.9.0a6 |
| docs/cli/submit.rst | Description, beside the help include: `--from-json`, `--repeat`, `--update`, `--zone` | versionadded | #48 #50 #78 | 2.9.0a1 (#48, #50), 2.9.0a4 (#78) |
| docs/cli/submit.rst | Description, beside the help include: re-submission refused; `-q` needs `--no-db` | versionchanged | #50 #78 | 2.9.0a1 (#50), 2.9.0a4 (#78) |
| docs/cli/cluster.rst | Description, beside the help include: `--from-json`, `--repeat`, `--update`, `--zone` | versionadded | #48 #50 #78 | 2.9.0a1 (#48, #50), 2.9.0a4 (#78) |
| docs/cli/cluster.rst | Description, beside the help include: file-aware `--restart`, re-submission refused | versionchanged | #50 | 2.9.0a1 |
| docs/cli/client.rst | Description, beside the help include: `--zone` | versionadded | #78 | 2.9.0a4 |
| docs/cli/client.rst | Description, beside the help include: requests its bundles; `-b` is the requested size | versionchanged | #78 | 2.9.0a4 |
| docs/cli/server.rst | Description, beside the help include: per-client bundles, `server.bundlemax` | versionchanged | #78 | 2.9.0a4 |
| docs/cli/initdb.rst | Description, beside the help include: in-place upgrade; `--rotate` sets `part` | versionchanged | #69 #59 | 2.9.0a2 |
| docs/cli/task_search.rst | Description, beside the help include: `--all`, `--part`, `card`, `--cancelled`, `--signal` | versionadded | 961fb22 #59 #60 27db1b0 | 2.9.0a1 (961fb22, 27db1b0), 2.9.0a2 (#59, #60) |
| docs/cli/task_search.rst | Description, beside the help include: 1,000-result guard; colorized output | versionchanged | 961fb22 210a93e | 2.9.0a1 |
| docs/cli/task_update.rst | Description, beside the help include: `--cancelled`, `--signal`, `SIGHUP` cancellation | versionadded | 27db1b0 | 2.9.0a1 |
| docs/cli/task_info.rst | Description, beside the help include: `card` format | versionadded | #60 | 2.9.0a2 |
| docs/cli/task_wait.rst | Description, beside the help include: `card` format | versionadded | #60 | 2.9.0a2 |
| docs/cli/task_submit.rst | Description, beside the help include: `--zone` | versionadded | #78 | 2.9.0a4 |
| docs/_include/templates.rst | § Named Fields | versionadded | #48 | 2.9.0a1 |
| docs/_include/templates.rst | § Execution Slot | versionadded | #52 | 2.9.0a1 |
| docs/_include/database.rst | § Initialization: in-place upgrade | versionchanged | #69 | 2.9.0a2 |
| docs/_include/database.rst | § Relay Database | versionadded | #82 | 2.9.0a6 |
| docs/_include/database.rst | § TimescaleDB | versionadded | #71 | 2.9.0a3 |
| docs/_include/config_task_env.rst | after the `TASK_SLOT` / `TASK_SLOT_COUNT` entries | versionadded | #52 | 2.9.0a1 |
| docs/_include/config_param_ref.rst | after `[database] .provider` (html only) | versionchanged | #71 | 2.9.0a3 |
| docs/_include/config_param_ref.rst | after `[database] [timescale]` (html only) | versionadded | #71 | 2.9.0a3 |
| docs/_include/config_param_ref.rst | after `[server] .bundlesize` (html only) | versionchanged | #78 | 2.9.0a4 |
| docs/_include/config_param_ref.rst | after `[server] .bundlemax` (html only) | versionadded | #78 | 2.9.0a4 |
| docs/_include/config_param_ref.rst | after `[submit] .bundlesize` (html only) | versionchanged | #78 | 2.9.0a4 |
| docs/_include/config_param_ref.rst | under `[relay]` (html only) | versionadded | #82 | 2.9.0a6 |
| docs/getting_started.rst | § Features, after the task-slot example | versionadded | #52 | 2.9.0a1 |
| docs/getting_started.rst | § Features, after the JSON-plan example | versionadded | #48 | 2.9.0a1 |
| docs/install.rst | § Docker and Apptainer, after the published-image passage | versionadded | ee03014 | 2.9.0a1 |
| docs/logging.rst | § Per-process log files, after the lock-sidecar passage | versionchanged | #54 | 2.9.0a2 |
| docs/security.rst | § Components: in-process queue server | versionchanged | #76 | 2.9.0a4 |
| docs/security.rst | § Relays | versionadded | #82 | 2.9.0a6 |
| docs/api/data.rst | § Functions: `initdb`, `checkdb`, `ensuredb` upgrade in place | versionchanged | #69 | 2.9.0a2 |
| docs/api/data.rst | after `autofunction:: upgradedb` | versionadded | #69 | 2.9.0a2 |
| docs/api/client.rst | after `autofunction:: run_client`: `zone` | versionchanged | #78 | 2.9.0a4 |
| docs/api/client.rst | after `autoclass:: ClientThread`: `zone` | versionchanged | #78 | 2.9.0a4 |
| docs/api/cluster.rst | § Classes: `zone` on every cluster class | versionchanged | #78 | 2.9.0a4 |
| docs/api/cluster.rst | after `autoclass:: AutoScalingCluster`: `evict_after` | versionchanged | #82 | 2.9.0a6 |

## Release-note items that need no marker

**Already in 2.8.5** (backported to the 2.8 line; the baseline has them):

- Group advance waits for a pending retry (#68): 2.8.4.
- TLS client dials IPv4 only (#72): 2.8.4.
- Task files given by name load fully before scheduling; groups run in ascending order; the client
  `-T` clock and `hs cluster` client start (#74): 2.8.4. Listed as an upgrade caution on the What's
  new page for readers coming from 2.8.3 or earlier.
- Run key handed to launched clients in the environment or over ssh stdin; tasks and `{% %}`
  commands no longer see it; no-key hints; template environment docs (#80, #81): 2.8.5. Listed as
  an upgrade caution for readers coming from 2.8.4 or earlier.
- `TCP_NODELAY` on queue connections (#76): 2.8.5. The in-process queue server from the same PR is
  2.9 and is marked.

**Fixes and internals with no behaviour a reader configures or sees in the docs:**

- Results applied only from the current assignment (#70), writeback statements built once (#73),
  lost-write recovery under TimescaleDB 2.29+ (#75): correctness and performance; no prose
  describes them, and no wire or schema change.
- Open-file reserve on the queue server (#76): an internal limit with a WARNING; no docs prose.
- Staged tasks returned to unscheduled on stop, and the "waiting on zone" log line (#78): part of
  zone dispatch, covered by the `--zone` and client-request markers; no separate prose.
- Fail-fast work-request protocol check between mismatched servers and clients (#78): no docs prose
  describes it; carried as an upgrade caution on the What's new page.
- Remapped exit-status sentinels for template and resource errors (27db1b0): no docs prose gives
  the old or new values. A candidate seed, not a marker.
- 2.9.0a1 fixes: inline `memory:` tags parsed to bytes, local config loading, broken pipes, task
  cancellation scheduling errors. Each restores documented behaviour.
- Zsh completion escaping (#55), the `cron` extra hint (#56): completion and error-message fixes;
  the `cron` extra's install text predates 2.9.

**Docs, dependencies and project work, not behaviour:**

- Top-level command names in the docs (#77), the `{= =}` lambda wording in the security docs (#85),
  the SSH and `SSHConnection` corrections on the security page: wording that now matches 2.8.5
  behaviour.
- Tutorial, blog and roadmap pages moved off the site, with redirects (#58); the new Alternatives
  page (c07d594), a comparison of tools that describes no HyperShell behaviour.
- `uv.lock` security refresh (#64, also 2.8.3), jinja2 3.1.6, soupsieve 2.9.1.
- CI, harness and release tooling (#63, #67, #83, #87, #88, SHA-pinned actions, setup-uv).

**Covered by a neighbouring marker:**

- `install.rst`'s `uuid7` extra note for the TimescaleDB provider: one sentence pointing at the
  TimescaleDB section, which is marked.
- `api/data.rst`'s intro paragraph on running `ensuredb` first: the Functions marker covers it.
- `cli/index.rst`'s relay toctree entry: navigation to the marked relay page.
- `manual.rst` and the `*_alt.rst` includes: man-page sources, out of scope (GOAL non-goals).
