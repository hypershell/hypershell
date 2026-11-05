# 05 — CLI surface, cluster propagation, docs and completions (Z2)

Line numbers are for the worktree at `b54d685`.

## 1. Submit side (R6)

- **`hs submit`**: the CLI is declared in `src/hypershell/submit.py:1239-1316`, with help text at
  `:1191-1236`. In DB mode, `run()` (`:1323`) resolves tags and group (`:1337`), then calls
  `prepare_source()`. Every DB path wraps the stream in a `GatedSource` (`:1455,1461,1478,1484`),
  and `Loader` unwraps it (`:304`) and stamps `source=` on each `Task.new` (`:351,382`). The
  single-command path calls `Task.new` directly (`:1363`).
  - **Recommendation**: carry the zone the same way `source_id` travels. Add `zone_id` to
    `GatedSource` (`:95-124`; its docstring explains that this avoids changing about 10
    constructor signatures), have `Loader` stamp it, and pass `zone=` at `:1363`. Resolve the name
    once in `run()`'s DB branch.
- **`hs task submit`**: `TaskSubmitApp`, `src/hypershell/task.py:64-115`. It has only
  `-t/--tag`; `Task.new(args, tag)` is at `:104`. Add `--zone` and resolve it before
  `Task.new`. `hs task run` (`:410-431`) is not named in R6, so leave it out.
- **Queue / `--no-db`**: `-q` puts bundles straight onto the server queue
  (`QueueCommitter.commit`, `submit.py:740`). There is no database, so no `Zone` table and no
  get-or-create. **Recommendation**: reject `--zone` with `-q`, and with `hs cluster --no-db`,
  as an `ArgumentError` ("--zone requires a database"). Put the checks in
  `SubmitApp.check_arguments` (`:1318`) and `ClusterApp.check_arguments`
  (`cluster/__init__.py:346`). The data brief should decide what a `--no-db` server answers to
  a zoned client. Option for that brief: a name-derived UUID (uuid5) would need no lookup and
  would make get-or-create race-free.
- **Config keys**: none in Z2. Precedent: `-g` and `-t` have no config key. The env var
  `HYPERSHELL_ZONE` is reserved for Z3 (R15), and `Environ(prefix='HYPERSHELL')` would read it
  as a top-level config key, so adding config keys now risks a clash. Make `--zone` long-only;
  `-z` is free on all three commands if a short flag is ever wanted.
- **Do not** treat a `zone:` tag as the zone, because existing users' tags would change meaning.
  **Flag for the data brief**: the task `fingerprint` (args + group + tags, `model.py:299`)
  ignores zone. As a result, `--update` of the same file under a different `--zone` de-duplicates
  every task to "present".

## 2. Client side (R2, R6)

`ClientApp`, `client.py:1486-1592`:

- `-b` defaults to **`config.submit.bundlesize`** (`:1508`). The documented `[client].bundlesize`
  (`docs/_include/config_param_ref.rst:298-308`) is not read by the CLI.
- **How to tell explicit from default (R2)**: make the CLI default `None`. Then advertise
  CLI > env/config, where env/config counts only when `config.which('submit','bundlesize')`
  (or `'client'`) is not `'default'`. I verified that `config.which` returns `default` and
  `env` as expected. Keep the effective `bundlesize` (used by the collector and the inbound and
  outbound `Queue(maxsize=…)`, `:1220-1226`) at today's value.
- Add `--zone NAME`. Change the help at `:1461` from "Bundle size for finished tasks" to say the
  value is both the size the client requests per bundle (when set) and the return-bundle size.
  Update `client_desc.rst` ("pulls bundles of tasks off the shared queue") and
  `config_param_ref.rst` [client] and [server] `.bundlesize`.

## 3. Cluster propagation (R6)

There is no shared argv builder (invariants §11).

| Mode | Client gets `-b` today? | Where | `--zone` goes |
|---|---|---|---|
| local (`LocalCluster`) | yes, as a kwarg, the same value as the server | `cluster/local.py:234-236` `ClientThread(bundlesize=…)` | `ClientThread(zone=…)` |
| launcher / mpi (`RemoteCluster`) | yes, always `-b str(bundlesize)` | `cluster/remote.py:303-306` | `client_args` at `:281-301` |
| ssh (`SSHCluster`) | yes, always | `cluster/ssh.py:296-300` | `client_args` at `:275-295`; **shell-quote it** (see below) |
| autoscaling (`AutoScalingCluster`) | yes, in the static argv | `cluster/remote.py:832-836` | `client_args` at `:813-831` |

- **R2 subtlety**: every mode sends the cluster's `-b`, and its default is
  `config.server.bundlesize` (`cluster/__init__.py:146`). That is the same value the embedded
  server uses (`local.py:219`, `remote.py:261`, `ssh.py:256`). A launched client therefore always
  advertises a size equal to the server's `-b`, which is behaviourally identical to advertising
  nothing.
  - **Recommendation**: keep today's argv. Do not propagate whether `-b` was explicit; there is
    nothing to gain and it would add four edits.
- **Autoscaler**: the Non-goals list "the autoscaler appending `--zone`". In context
  (seed Q4/R15) that means per-zone `--zone` for zone-aware autoscaling (Z3). It does not mean
  passing the cluster's own static `--zone`. Appending a static `--zone` to the `:832-836` argv
  is the same as `-b` and satisfies R6. Z3 later replaces it with a per-launch zone.
- **Cluster submit stamping, required**: `hs cluster --zone X FILE` must also stamp X on the
  tasks it submits from FILE. Otherwise its zone-X clients never see the default-zone FILE tasks
  and the run never finishes. Use the same `GatedSource.zone_id` route, built in
  `ClusterApp.prepare_source` and `prepare_json_source` (`cluster/__init__.py:502,508,528,532`).
  No change to the `ServerThread` or launcher signatures is needed.
- **Zone values in SSH mode**: SSH mode re-parses the client argv on the remote side; the
  template is hand-quoted at `ssh.py:299`. Pass the zone through `shlex.quote`. Alternatively,
  validate names against a conservative pattern at every entry point. GOAL says names are
  "arbitrary user text", so a pattern needs a GOAL clarification.
- `hs server --zone` is not required by R6, so leave it out.

## 4. Docs and completions (R13)

- **`docs/_include` is hand-maintained.** There is no generator; edit each file to mirror the
  help constant (precedent: `spec/part-tag-to-column/research/06-*.md`).
  - submit: `submit_usage.rst`, `submit_help.rst`
  - task submit: `task_submit_usage.rst`, `task_submit_help.rst`
  - client: `client_usage.rst`, `client_help.rst` (and `-b`), `client_desc.rst`
  - cluster: `cluster_usage.rst`, `cluster_help.rst`
  - server (`-b` meaning only): `server_help.rst`, `server_desc.rst:6`
  - config reference: `config_param_ref.rst`
- **Completions**: one file per shell. `hsx` is a symlink in bash and a `#compdef` in zsh, so
  editing `_hs_cluster` covers it.
  - bash `share/bash_completion.d/hs`: `_hs_task_submit` `:268`, `_hs_submit` `:519`,
    `_hs_client` `:574`, `_hs_cluster` `:655`. Add `--zone` to `all_opts`. A zone value takes
    free text, so no `COMPREPLY` branch is needed.
  - zsh `share/zsh/site-functions/_hs`: `_hs_task_submit` `:340`, `_hs_submit` `:463`,
    `_hs_client` `:495` (its `-b` description is at `:499`), `_hs_cluster` `:559`. Use the form
    `'--zone[Zone name …]:zone:'`, keeping the brackets balanced.
- **Man pages**: `share/man/man1/{hs,hsx,hyper-shell}.1` are generated from `docs/manual.rst`
  (`docs/conf.py:153`). They are rebuilt at release (`/hs-release`); feature commits normally
  leave them alone.
- **CI**: the wheel metadata job (`.github/workflows/tests.yml:103-108`) lists six fixed `share/`
  paths. No new files, so no change.
- **Tests that guard this**:
  - `tests/test_completions.py` only lints zsh `_arguments` description brackets
    (`iter_bad_arg_specs`, plus the `--from-json` escape). Nothing checks that completions match
    the CLI.
  - `tests/test_main.py:73-77` asserts `hs client -h` equals `interface.help_text`, which is
    automatic.
  - Add `--zone` to the `OPTION_PAIR` missing-value tables in `test_submit.py:77`,
    `test_cluster.py:106` (and `test_server.py:87` only if the server gets a flag).

## 5. Display (R12)

The display paths all go through `task.py`:

- `format_task_fields` (`:1440-1460`) feeds both the normal view (`NORMAL_MODE_TEMPLATE`
  `:1314`) and the card view (`render_card` `:1515`).
- `hs info`: `--json`/`--yaml` use `task.to_json()` (`:231`); `-x` is at `:215`.
- `hs list`: `print_{plain,table,json,csv}` (`:778-838`) print raw values.
- `hs wait --info` and `hs run` reuse `TaskInfoApp`.

How `source` is shown today: the path appears in normal and card views (`resolve_source`,
batched through `Source.paths_for_ids`, `:793,803`), and the raw UUID appears in
plain/table/json/csv.

**Minimal change**:

- Add `Zone.names_for_ids()`, mirroring `Source.paths_for_ids` (`model.py:1068`).
- Add `data['zone']` (the name, with `None` mapped to the default-zone name) and
  `data['zone_id']` in `format_task_fields`.
- Normal view: add a line ` zone: {zone} ({zone_id})` after `source:`.
- Card view: the comment at `:1538` expects zone in the identity header, but the header grids
  hardcode `range(4)`/`range(3)` (`:1545,1552`), and a long name won't fit at 130 columns. Put
  `('zone', name)` in the full-width `submission` region instead (`:1559`); no header test
  changes then.
- Resolve the name in `hs list` plain/table/json/csv and in `hs info` json/yaml/`-x zone`, in the
  display layer only.
  - **Never change `Task.to_json()`**: it is the wire format (`model.py:152,239`) and the source
    of the task environment (`client.py:441`).

**Compatibility shape**:

- The new column changes `hs list --json/--csv` with all fields (`ALL_FIELDS = list(Task.columns)`,
  `:440`), the `hs info --json` keys, `--fields`, and `-x` choices.
- Every task gains a `TASK_ZONE=<uuid>` environment variable (`client.py:441-452`). Either
  document it or set it to the name.
- Keep `tag` last in the model (`model.py:303`).

## 6. Server cap

- **Recommendation: config-only**, `server.bundlemax` in `core/config.py:128-135`, documented
  only in `config_param_ref.rst` [server]. Completions pick it up through
  `hs config get --list-available`.
- A flag would touch `server.py` help and CLI, all four cluster modes plus `ClusterApp`, 4 docs
  files and 2 completion blocks. Per-zone settings are a non-goal, and R13 asks for docs only
  "if user-facing".
- Make the effective cap `max(bundlemax, server -b)`, so the server's own `-b` (used for
  unadvertised clients) is never shrunk (R3).

## 7. Size (files per change, excluding tests)

| Change | Files | Count |
|---|---|---|
| submit `--zone` | `submit.py`, 2 rst, 2 completions | 5 |
| task submit | `task.py`, 2 rst, 2 completions | 5 |
| client `--zone` and `-b` | `client.py`, 3 rst, `config_param_ref`, 2 completions | 7 |
| cluster `--zone` | `cluster/__init__.py`, `local.py`, `remote.py`, `ssh.py`, `submit.py` (GatedSource, shared), 2 rst, 2 completions | about 9 |
| server `-b` wording | `server.py`, 2 rst | 3 |
| display | `task.py`, `model.py`, about 3 test files | — |
| cap (config-only) | `core/config.py`, `config_param_ref.rst` | 2 |

One CLI phase can carry all four `--zone` flags in one commit, with each completion file edited
once.

## Recommendation

- **Flags**: add a long-only `--zone NAME` on `hs submit` (DB mode; an error with `-q`),
  `hs task submit`, `hs client` and `hs cluster` (an error with `--no-db`). Add no config or env
  keys in Z2. Add one config-only cap, `server.bundlemax`.
- **Client `-b`**: change the CLI default to `None` and advertise when the value came from the
  CLI, or when `config.which(...)` is not `'default'`.
- **Propagation**:
  - local: `ClientThread(zone=)`
  - remote: `client_args += ['--zone', zone]` (`remote.py:281`)
  - autoscale: same, at `remote.py:813`
  - ssh: `ssh.py:275`, shell-quoted
  - Keep the `-b` argv unchanged. Stamp cluster-submitted tasks through `GatedSource.zone_id`.
- **Regeneration procedure**:
  1. Edit `docs/_include/*.rst` by hand to match `--help`.
  2. Edit `share/bash_completion.d/hs` and `share/zsh/site-functions/_hs`.
  3. Run the verify gate: `uv run pytest tests/test_completions.py tests/test_main.py
     tests/test_submit.py tests/test_cluster.py`, plus `bash -n share/bash_completion.d/hs`,
     `zsh -n share/zsh/site-functions/_hs`, and `uv run sphinx-build docs docs/_build` with no
     new warnings.
  4. Optionally add a unit test that each app's long options appear in its completion block.
  5. Leave man pages to `/hs-release`.
- **R12**: name in all human formats; `zone: name (uuid)` in normal view, a submission-region
  row in the card, and names in list/info json/csv. Never touch `to_json()`.
- **Risks**:
  1. Zoned clients from `hsx --zone` against FILE tasks that were not stamped will hang the run.
  2. The two-key `-b` default (`submit.` vs `client.`).
  3. Quoting zone names in SSH mode.
  4. `hs update zone=<raw>` is accepted today (`task.py:1030`), because any column is
     updatable. Consider rejecting it.
  5. `auto_union_sqlite` unions partitions with `select *` (`task.py:866-890`). An upgraded main
     database (column appended) and an older or newer partition can differ in column order or
     count. Flag this for the data brief.
  6. `TASK_ZONE` exposes the UUID.
