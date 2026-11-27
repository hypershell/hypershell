# Research 03 — launcher zone exposure, docs surface, laptop-scale CLI verify

Scope: R3 (`{zone}` / `HYPERSHELL_ZONE` / appended `--zone`, docs and `share/`), plus the CLI-drive
verify for the cycle. Read against `8a85d46`.

## Findings

### TASK_SLOT's dual exposure, the model to mirror
- Env: `task_env()` adds `'TASK_SLOT'`/`'TASK_SLOT_COUNT'` last, so they win
  (`src/hypershell/client.py:490-517`, `:515-516`).
- Substitution: the client passes a named context to the task template,
  `self.template.expand(args, context={'slot': …, 'slot_count': …})` (`client.py:780-781`);
  `Template._expand` checks simple patterns, then `context`, then complex patterns
  (`core/template.py:98-118`, `PATTERN = r'{(.*?)}'` at `:28`). That is the **task** template
  machinery. `{zone}` must not use it: it belongs to launcher words, which `Template` never sees, and
  GOAL forbids touching `-t`.

### How a launch is built today
- `client_launch()` (`cluster/launch.py:126-197`) builds the options once
  (`client_options`, `:305-349`; `--zone` is appended at `:347-348` only when `zone is not None`).
  Launcher path: `argv = [*launcher, *launcher_args, remote_exe, 'client', *options]`,
  `env = {AUTH_ENV: auth}` (`:191-197`); for an Open MPI first word it adds
  `OMPI_MCA_mca_base_env_list` or `-x HYPERSHELL_SERVER_AUTH` (`:192-196`). SSH path: env `{}`,
  `inherit_env=True`, key on stdin, client options inside a quoted `sh -c` script (`:185-190`).
- `ClientLaunch` (`:69-123`) is frozen: `argv`, `env`, `stdin`, `inherit_env`. `start()` merges
  `os.environ` → task env → `self.env` (`:93`). `describe()` redacts only key words/options (`:110-123`).
  It does **not** record where the launcher words end, so a post-hoc `for_zone()` cannot safely tell a
  launcher `{zone}` from a `{zone}` inside the `-t` value, nor insert `--zone` into an SSH script.
- `AutoScalingCluster` builds **one** launch (`cluster/remote.py:815-823`, `launcher` split with
  `shlex.split`, `launcher_args` split per group) and hands it to `AutoScalerThread` (`:824`);
  `AutoScaler.start` logs `describe()` (`:431-435`), `scale()` calls `self.launch.start(...)`
  (`:524-531`). Autoscaling never uses SSH (`--autoscaling` with `--ssh`/`--mpi` is rejected,
  `cluster/__init__.py`, `check_arguments`), but `--launcher=mpirun …` is allowed.
- Relay: `zone_launch()` already rebuilds per zone through `client_launch(zone=zone)`
  (`relay/site.py:121-143`). Tests pin the shape: `zone_word(default.machine.launch.argv) == []`
  (`tests/test_relay_launch.py:641`), `cluster.autoscaler.machine.launch is cluster.launch` and the exact
  autoscale argv (`tests/test_cluster_launch.py:629-638`).

### Zone names, default zone, and what the client must get
- `DEFAULT_ZONE_NAME = 'default'`, `DEFAULT_ZONE_ID` fixed (`data/model.py:96-99`); `zone_id()` maps
  None/''/`'default'` to the default id, others via `uuid5` (`:122-126`) — one-way, so the autoscaler
  must map discovered task zone ids to names with `Zone.names_for_ids` (`:1533-1537`). Default-zone tasks
  store NULL and its row is not required (`:1471-1478`): map NULL/`DEFAULT_ZONE_ID` → `'default'`
  explicitly. Rows are written by `Zone.ensure` at `hs submit` (`submit.py:1367`), `hs task submit`
  (`task.py:113`), `hs cluster --zone` (`cluster/__init__.py:548`) and server start for the default
  (`server.py:214`). A zone id with no row (e.g. a relay-local DB) has no recoverable name: skip it with a
  warning.
- `Dispatcher.zone_name(zone)` (`core/dispatch.py:427-431`) only knows names clients have asked for —
  not usable for discovery.
- Client side: `--zone` is checked by `zone_name()` (strip, non-empty, ≤256, printable;
  `client.py:1708-1711`); the server resolves the name with `zone_id()` (`core/dispatch.py:106-110`).
  So `--zone default` is accepted and equals no `--zone` (also on 2.9.0a4, whose `zone_id` already
  reserves `'default'`). Names may contain spaces.
- Contract test (`.agents/factory/invariants.md` §11; `tests/test_cluster_launch.py:942-988`):
  `CONTRACT_CLIENT_OPTIONS` already lists `'--zone': True`; 2.9.0a4's client defines it. **`--zone`
  passes.**

### `HYPERSHELL_ZONE` side effects (checked)
- It sits under the config env prefix: `Environ(prefix='HYPERSHELL').expand()`
  (`core/config.py:249-251`), so in any launched `hs` process it becomes config key `zone`
  (`HYPERSHELL_ZONE=gpu hs config get zone` → `gpu`; `=123` → int `123`). Nothing reads `config.zone`
  today, so it is inert; the client must keep `--zone` authoritative and never read `config.zone`.
- Tasks inherit it: `inherited_task_env()` strips only the key (`client.py:475-487`), so a task on an
  autoscaled client sees `HYPERSHELL_ZONE` when the launcher passes the environment through. Useful and
  harmless; the verify below uses it.
- Open MPI: do **not** forward it. The shim/launcher reads it on the launch host; remote ranks already get
  `--zone` on argv. Forwarding would change the key-forwarding contract the tests pin.
- `describe()`: nothing to redact — the zone name is not secret; the env is not in the description.

### Substitution mechanics and back-compat
- Use `word.replace('{zone}', name)` per launcher word **after** `shlex.split`: names with spaces stay one
  word, `--partition={zone}` works, and other braces (`${VAR}` inside `sh -c '…'`, `{}`) are untouched.
  `str.format` would raise on `${HOME}`/`{}`.
- Today a literal `{zone}` passes through unchanged (observed: launcher word `{zone}` reached the shim
  verbatim). A user relying on a literal `{zone}` is implausible; note it in the `versionchanged`.

### Docs and `share/` surface (R3)
No separate autoscaling prose page exists; the "autoscaling section" is the `--autoscaling` entry.
- `docs/_include/cluster_help.rst`: `--launcher` (`:19-23`) — `{zone}`/`HYPERSHELL_ZONE` under
  `--autoscaling`; `--zone` (`:119-124`) — reword for per-zone launches (R2 touches this too);
  `--autoscaling` (`:326-345`) — per-zone scaling and the **worked shim example**.
- `docs/_include/cluster_desc.rst:31-32` — one sentence on per-zone launches.
- `docs/_include/config_param_ref.rst:526-531` `[autoscale] .launcher` — same substitution applies to the
  configured launcher; markers inside this include are wrapped in `.. only:: html` (see `:558-560`).
- `docs/cli/cluster.rst:18-23` — `.. versionadded:: 2.9.0` / `.. versionchanged:: 2.9.0` beside the
  include (generated help snippets are never hand-marked; `AGENTS.md:503-512`). Release line: 2.9.0
  (`pyproject.toml:3` is `2.9.0a7`). No PR number during the cycle; `hs-release` adds it.
- In-code help: `src/hypershell/cluster/__init__.py` `APP_HELP` (`--launcher` `:75`, `-A` `:114`)
  if wording changes.
- `share/zsh/site-functions/_hs:609` (`--launcher[…]`) and `:596` (`--zone[…]`) descriptions; bash
  completion (`share/bash_completion.d/hs:686-690`) has no descriptions — no new option, so likely no
  change. `tests/test_completions.py` checks option parity.
- Man pages (`share/man/man1/{hs,hsx,hyper-shell}.1`) are rebuilt only at release
  (`.agents/skills/hs-release/SKILL.md:179-190`); Z2 (#78) and relay (#82) did not touch them. No CI
  metadata change (no new `share/` path; `pyproject.toml:120-125`, `.github/workflows/tests.yml:182-187`).
- Docs gate: `uv run --python 3.12 --group docs sphinx-build -W --keep-going -q -E -b html docs docs/_build/html`
  (`AGENTS.md:503-505`).

### Laptop-scale drive: rehearsed
`temp_site.sh` (`.agents/factory/bin/temp_site.sh`) exports a throwaway site, `UV_PROJECT`, and `cd`s
into the site, so the script path must be absolute (`"$PWD/…"`, expanded by the outer shell). macOS has
no `timeout`; bound the run with a poll loop. Autoscaling implies `--forever`, so the drive backgrounds
`hs cluster --restart` (bare: empty source, no stdin read), polls `hs list --remaining --count` until 0,
kills the cluster, then waits for the logged client pids (they exit on `-T 2`). The cluster advertises the
host name to clients, so the shim rewrites `-H` to loopback (as the tests' stand-in does,
`tests/test_cluster_launch.py:99-104`). `hs submit` without `-g` auto-selects a group — always pass `-g`.
Rehearsed against current code: default-zone-only run completes in ~4 s; the three-zone run FAILs as it
should — the fixed policy relaunches default-zone clients every ~2 s on cpu-zone work it cannot serve (the
R1 bug), `word={zone}`, no env, no `--zone`.

## Recommendations

1. **Build per zone through `client_launch`, not `ClientLaunch.for_zone`.** Add a keyword (e.g.
   `zone_launcher: bool = False`) to `client_launch`: when set (launcher path only), substitute `{zone}`
   in `[*launcher, *launcher_args]` with the zone name and add `HYPERSHELL_ZONE=<name>` to `env`;
   `--zone` keeps coming from `client_options(zone=…)`. The autoscaler holds a launch factory
   (a `functools.partial` over `client_launch` with the auth key bound) and caches one `ClientLaunch`
   per zone name — the actuator the GOAL's demand/actuation note asks for, reusable by relay-topology.
   Keep `AutoScalingCluster.launch` (its own zone's launch) and `machine.launch` so the relay and its
   tests stay green; keep `relay/site.py::zone_launch` without the flag (no relay behaviour change).
2. **Default zone:** `{zone}` → `default`, `HYPERSHELL_ZONE=default`, and **no** `--zone` (argv
   identical to today; `tests/test_relay_launch.py:641` and `test_cluster_launch.py:633` stay true). R3's
   "appends `--zone`" is then satisfied for every non-default zone; flag this reading at the plan gate
   (the alternative, `--zone default`, is accepted by a4 clients but changes today's argv).
3. Log `describe()` once per zone at its first launch (`Autoscale launcher (zone gpu): …`).
4. Unit tests: substitution in a launcher word and in `--opt={zone}`; `-t '{zone} {}'` untouched; a
   name with a space stays one argv word; `${HOME}` untouched; env carries `HYPERSHELL_ZONE`; no OMPI
   forwarding change; contract test parametrized with the flag.
5. Ship the drive as evidence tooling in `spec/zone-autoscale/drive/` (precedent: `spec/relay/drive/`).

Shim, `spec/zone-autoscale/drive/zone_shim.sh`:

```sh
#!/bin/sh
# Launcher shim for the zone-autoscale drive: --launcher="sh zone_shim.sh LOG {zone}".
log=$1 word=$2
shift 2
client=- prev=
for arg do
    shift
    case $prev in
        -H) arg=127.0.0.1 ;;
        --zone) client=$arg ;;
    esac
    set -- "$@" "$arg"
    prev=$arg
done
printf 'launch pid=%s word=%s env=%s client=%s\n' "$$" "$word" "${HYPERSHELL_ZONE:--}" "$client" >> "$log"
exec "$@"
```

Drive, `spec/zone-autoscale/drive/zone_drive.sh` (rehearsed; SPDX header and usage comment omitted):

```sh
#!/bin/sh
set -eu
here=$(cd "$(dirname "$0")" && pwd)
deadline=${ZD_DEADLINE:-120}
log=$PWD/launches.log out=$PWD/tasks.log
: > "$log"; : > "$out"
for pair in default:0 cpu:0 gpu:1; do
    zone=${pair%%:*} group=${pair#*:}
    for n in 1 2 3; do
        printf '%s\n' "sh -c 'echo $zone \${HYPERSHELL_ZONE:--} $n >> $out'"
    done > "tasks-$zone.in"
    if [ "$zone" = default ]; then
        uv run --quiet hs submit -g "$group" -f "tasks-$zone.in"
    else
        uv run --quiet hs submit -g "$group" --zone "$zone" -f "tasks-$zone.in"
    fi
done 2>/dev/null
port=$(uv run --quiet python -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
uv run --quiet hs cluster --restart --autoscaling=fixed --launcher="sh $here/zone_shim.sh $log {zone}" \
    -H 127.0.0.1 -p "$port" -T 2 -P 1 -I 1 -X 0 > cluster.out 2>&1 &
pid=$!
waited=0
while [ "$(uv run --quiet hs list --remaining --count)" != 0 ]; do
    waited=$((waited + 1))
    if [ "$waited" -ge "$deadline" ]; then echo "zone_drive: tasks remain after ${deadline}s" >&2; break; fi
    sleep 1
done
kill "$pid" 2>/dev/null || true
wait "$pid" 2>/dev/null || true
for client in $(sed -n 's/^launch pid=\([0-9]*\) .*/\1/p' "$log"); do
    while kill -0 "$client" 2>/dev/null; do sleep 1; done
done
echo '--- launches'; cat "$log"
echo '--- tasks'; sort "$out"
status=0
awk '{ for (i = 3; i <= 5; i++) { split($i, kv, "="); v[i] = kv[2] }
       if (v[3] != v[4]) bad = 1
       if (v[5] != v[3] && !(v[3] == "default" && v[5] == "-")) bad = 1
       seen[v[3]] = 1 }
     END { exit (bad || !seen["default"] || !seen["cpu"] || !seen["gpu"]) }' "$log" || status=1
awk '$1 != $2 { bad = 1 } { n[$1]++ }
     END { exit (bad || n["default"] != 3 || n["cpu"] != 3 || n["gpu"] != 3) }' "$out" || status=1
[ "$(uv run --quiet hs list --failed --count)" = 0 ] || status=1
echo "zone_drive: $([ "$status" = 0 ] && echo PASS || echo FAIL)"
exit "$status"
```

Assertions: every launch has `word == env`, and `client == word` (default zone: `-`; tighten to one form
once rec. 2 is decided); each of default/cpu/gpu launched; each task's `HYPERSHELL_ZONE` equals its
zone (routing plus env reached the task); 3 tasks per zone; no failures. An R2 extension: have the shim
also log `hs list -g 0 --remaining --count` and assert it is 0 on every `gpu` line.

Draft `verify:` for the R3 phase (pass it to `set_phase.py --verify`, which does the YAML quoting; it
has no zsh hazards):

```
uv run pytest -v tests/test_cluster_launch.py tests/test_relay_launch.py && .agents/factory/bin/temp_site.sh sh "$PWD/spec/zone-autoscale/drive/zone_drive.sh"
```

Docs phase: `uv run --python 3.12 --group docs sphinx-build -W --keep-going -q -E -b html docs docs/_build/html && grep -q HYPERSHELL_ZONE docs/_include/cluster_help.rst && zsh -n share/zsh/site-functions/_hs && bash -n share/bash_completion.d/hs`.
