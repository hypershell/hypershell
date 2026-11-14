# Research 04 — One advertised address (R6, R7, R8)

Base: this worktree = `develop` (`9d1e1c5`). All paths under `src/hypershell/` unless noted.

## 1. Where clients are told an address today

| Site | Today | Bind it sees |
|---|---|---|
| `cluster/remote.py:288` `RemoteCluster` (launcher, `--mpi`) | `HOSTNAME if bind[0] == '0.0.0.0' else bind[0]` | `ClusterApp.run_launch`/`run_mpi` |
| `cluster/remote.py:881` `AutoScalingCluster` (`build = partial(client_launch, …, (HOSTNAME, bind[1]))`) | always `HOSTNAME` | `run_autoscaling`; also `relay/site.py:130` |
| `cluster/ssh.py:283` `SSHCluster` | always `HOSTNAME` | `run_ssh` |
| `relay/site.py:172` `zone_launch` (`(HOSTNAME, queue.port)`); `site_cluster` overwrites `cluster.launch` with it (`:142`) | always `HOSTNAME` | `queue = cluster.server.queue.config` (`:170`); `queue.host` is the bind (`server.py:1118`, `QueueConfig(host=address[0])`) |

`HOSTNAME = socket.gethostname()` (`core/logging.py:58`). Every other `HOSTNAME` use in `src/` is identity
stamping (task/client/server host, heartbeat, log names, `relay/upstream.py:167` site name), not a dial
address — none changes. The address reaches argv only through `client_launch` → `client_options`
(`cluster/launch.py:466-467`, `['-H', host, '-p', port, …]`).

Bind widening: `ClusterApp.run_launch/run_mpi/run_ssh/run_autoscaling` (`cluster/__init__.py:300,305,315,321`)
all do `bind = self.host if self.host not in ('0.0.0.0', 'localhost') else '0.0.0.0'`; `run_local`
(`:291-294`) refuses any host but `localhost`/`127.0.0.1`. `-H` defaults to `config.server.bind`
(`:208-209`, default `'localhost'`, `core/config.py:136`).

Relay: `--bind` defaults to `config.relay.bind` = **`'0.0.0.0'`** (`core/config.py:201`,
`relay/__init__.py:94,1083-1084`), passed as `address=(self.bind, self.port)` (`:1134`) to
`site_cluster` (`:530`) with **no** widening. So under R6 a default relay still advertises `HOSTNAME`:
**R6 does not change default relay behaviour**; only a relay given `--bind ADDR`/`relay.bind` now tells
its site clients `ADDR` (e.g. `--bind localhost` → `localhost`, which matches what it listens on; today
those clients dial a name that may not reach a loopback-only listener). Read-only: `relay/__init__.py` is
#103's; the change lives entirely in `relay/site.py:172`.

**Proposed helper** — `cluster/launch.py`, beside `client_launch`, exported in `__all__`
(`launch.py` already imports `core.logging`; add `HOSTNAME`):

```python
WILDCARD_BINDS: Final[FrozenSet[str]] = frozenset({'0.0.0.0', ''})
def advertised_host(bind: str, advertise: Optional[str] = None) -> str:
    """The address launched clients dial: `advertise` if given, the host's own name for a wildcard bind, else the bind."""
    if advertise:
        return advertise
    return HOSTNAME if bind in WILDCARD_BINDS else bind
```

`''` is INADDR_ANY to Python's `bind` and `ClusterApp` passes `-H ''` through unwidened; today
`RemoteCluster` would tell clients `-H ''` — treat it as wildcard. Call sites: `remote.py:288`
→ `advertised_host(bind[0], advertise)`, `remote.py:881` likewise, `ssh.py:283` likewise,
`relay/site.py:172` → `advertised_host(queue.host)` (no `advertise`: relay override is a non-goal).
IPv6 `::` falls out: the listener is AF_INET only (`core/queue.py:216-219`, `SecureListener` refuses other
families) and `socket(AF_INET).bind(('::', 0))` raises `gaierror` (probed), so a `::` bind fails at
server start before any launch; the helper would pass `::` through unchanged, never reached. Do not add
`::` to the wildcard set (that is `issues/ipv6-server-listener.md`).

Rejected: a method on each cluster class (four copies of one rule, relay can't share it); putting the
rule in `client_launch` itself (it takes a resolved address and the contract test calls it with one,
`tests/test_cluster_launch.py:1001-1002`; the R1 error also needs the address before launch); in
`core/logging.py` (identity module, not launch policy).

## 2. R7 precedence and plumbing

- **Key:** declare `'advertise': PARAM_UNSET` in `default['server']` between `host` and `port`
  (`core/config.py:136-138`). `PARAM_UNSET = '<none>'` (`:81`) is the convention for unset string keys
  (`server.tls.servername/fingerprint`, `relay.site`, `autoscale.sites`), and declaring it makes
  `HYPERSHELL_SERVER_ADVERTISE` and `hs config get server.advertise` behave like every other key. The
  absent-key form (`config.task.get('ratelimit')`, `cluster/__init__.py:234`) is for undeclared keys;
  don't use it for a documented one. Read as unset when `None`, `''` or `PARAM_UNSET`
  (same test as `relay/__init__.py:246` `_given`, `relay/upstream.py:167`).
- **Option:** `advertise: Optional[str] = None; interface.add_argument('--advertise', default=None)` —
  default **None, not the key**, so `check_arguments` can tell the option from the key. In
  `check_arguments` (`cluster/__init__.py:350ff`): `if self.advertise is not None and self.mode == 'local':
  raise ArgumentError('Cannot use --advertise with a local cluster (no launched clients)')`
  (exit 2 via cmdkit, as the other refusals). Suggest also refusing `--advertise ''` (empty value).
- **Key with a local cluster:** silently ignored — R7 refuses only the option, and `server.advertise` is
  a site-wide setting a local run must not trip on (same as `server.bind` being ignored by `run_local`).
- **Resolution** (one place, e.g. a `ClusterApp.advertise_host` cached property):
  `self.advertise if self.advertise is not None else (key if _given(key) else None)` → passed as a new
  `advertise: Optional[str] = None` kwarg by `run_launch/run_mpi/run_ssh/run_autoscaling` into
  `run_cluster`/`run_ssh` (both forward `**kwargs`; signatures unchanged) → `RemoteCluster.__init__`,
  `AutoScalingCluster.__init__`, `SSHCluster.__init__`.
- **Constructors must not read the key themselves.** `site_cluster` builds an `AutoScalingCluster`
  (`relay/site.py:129-137`); a relay sharing a site config with `server.advertise` set would otherwise
  leak it toward relay site clients (non-goal). Constructor default `None` = the R6 rule.
- Keep the resolved host on the instance (e.g. `self.advertised = (host, port)`) for the R1/R3 error
  text ("the address the clients were told").
- **Docstrings to update:** `bind (tuple, optional)` entries at `remote.py:131`, `:680`, `ssh.py:114`
  (say which address clients are told) + a new `advertise (str, optional)` entry each (after `zone`,
  `remote.py:207`, `:783`, `ssh.py:194`); `site_cluster` docstring `relay/site.py:118-120` ("dial this host
  by name" → the bind when it is one address).

## 3. TLS and the advertised name

Client side (`core/queue.py:317-321`): `servername = cfg.servername` if set; else, only when
`ctx.check_hostname`, the dialed host (`address[0]`) — i.e. the advertised address. `build_client_context`
(`core/tls.py:165-190`) sets `check_hostname` only in two modes: `cafile` + `servername` (checks
`servername`, independent of the dialed address) and the system-CA mode (`cafile` `<none>`, no
fingerprint, not insecure: `check_hostname=True` against the dialed host). Fingerprint pinning and
insecure skip the name check; default `cafile='<auto>'` mirrors the cert (`tls.py:426-427`) with
`check_hostname = bool(servername)` = False.
`<auto>` cert SANs (`tls.py:287-300`, `_local_san_entries` `:267-284`): FQDN (or `gethostname()` if the
FQDN is a `.arpa` PTR), short name, `localhost`, `127.0.0.1`, `::1` — **no other IPs**.

Consequences: under default `-H` (wildcard → `HOSTNAME`) nothing changes. A specific IP bind or
`--advertise IP` under default `<auto>` TLS works (no name check). Only in system-CA mode does the
advertised address matter: it must be a name on the server's certificate (an internal IP generally is
not) — that is the case `--advertise NAME` exists for. Doc sentence for R8: *"Under TLS the advertised
address must be one the client's certificate check accepts: with a system-CA certificate (no `cafile`,
no `servername`) clients check it against the address they dial, so advertise a name the certificate
carries, or set `server.tls.servername`."*

## 4. R8 docs inventory

- `cluster/__init__.py` `APP_USAGE` `:58` (`[-H ADDR]` → `[-H ADDR [--advertise ADDR]]` or beside it);
  `APP_HELP` `:81` (`-H` line) + new `--advertise ADDR` line after it.
- `docs/_include/cluster_usage.rst:3`, `cluster_help.rst:60-64` (rewrite `-H` body per R6; new
  `--advertise` entry with the TLS sentence), `cluster_desc.rst` (one paragraph: address rule + "exits
  non-zero when every client is gone with work remaining", R1/R3). These snippets are **hand-maintained**
  (richer than `APP_HELP`; no generator — `docs/conf.py:93` only excludes `_include`; prior cycles edited
  them by hand). Never hand-marked.
- `docs/cli/cluster.rst:18-19` — add `--advertise` to the `versionadded:: 2.9.0` list; a
  `versionchanged:: 2.9.0` for R6 (`--ssh`/autoscaling advertise a specific bind) and R1/R3 exit.
- Man pages `share/man/man1/{hs,hsx,hyper-shell}.1`: built from `docs/manual.rst` (includes
  `cluster_*.rst` `:69-73`, `config_param_ref.rst` `:259`) and rebuilt only at release
  (`.agents/skills/hs-release/SKILL.md:179-190`); every touch is a `[release]` commit (2.9.0a8's rebuild
  carried 169 lines of CLI content). Precedent: cycles don't edit them; R8's man page is met via the
  snippets. Plan should state this explicitly for `hs-review`.
- Completions: `share/bash_completion.d/hs:687-690` (`all_opts`) and `:732` (value case, e.g.
  `--advertise` → no completion or `_hs_known_hosts`); `hsx` is a byte-identical copy (diff empty) — edit
  both. `share/zsh/site-functions/_hs:590` (add `'--advertise[…]:address:'` after `-H`); one `_hs`
  serves `hs`/`hsx` (`#compdef hs hsx`). `tests/test_completions.py` only lints zsh `[...]` escaping
  (`:32-90`) — no option-parity check, so nothing fails if one is missed. Optional: config value
  completion `bash:183` / `zsh:272` (not needed).
- `docs/_include/config_param_ref.rst`: `server.advertise` after `.host` (`:176-182`; `.bind` `:170-174`
  should say "a cluster's clients are told …"); `autoscale.failed_launches` in `[autoscale]`
  (`:512-640`, e.g. after `.period` `:550-565`); markers inside this include are wrapped
  `.. only:: html` / `.. versionadded:: 2.9.0` (pattern `:597-600`). **#103 adjacency:** `.evict` at
  `:277-283` (directly below `.poll`) and the `[relay]` section `:640ff` — keep edits outside those
  blocks.
- `docs/security.rst` Peer Verification Modes (`:327-363`): one paragraph with the TLS sentence.
- Relay wording now wrong under R6 for a non-wildcard `--bind`: `docs/_include/relay_help.rst:42`
  ("connect back to the relay by its host name") and `relay/site.py:118-120`. `relay_help.rst` is the
  `relay/__init__.py` CLI snippet — likely touched by #103; coordinate (whole `hs relay` page is already
  `versionadded:: 2.9.0`, no new marker needed).
- Agent docs: `AGENTS.md:415-416` and `.agents/factory/invariants.md:185` ("Advertised `HOSTNAME` must
  be routable from clients") → "Clients are told `cluster/launch.py::advertised_host`: `--advertise`/
  `server.advertise`, else `HOSTNAME` for a wildcard bind, else the bind; it must be routable from
  clients (relay: the rule without override)."
- Not affected: `docs/getting_started.rst:132-148` (`hs server --bind`), `server_help.rst:14`.

## 5. Launch contract test

`tests/test_cluster_launch.py:962-1011`: `CONTRACT_CLIENT_OPTIONS` (2.9.0a4) includes `-H`/`--host`;
the test calls `client_launch` with a fixed `address` and checks option names only, never the `-H` value.
R6/R7 change only that value; `--advertise` is a server-side option and adds no client option. No test
asserts `HOSTNAME` on a launch argv today (grep `tests/`), so no existing test pins the old rule.

## 6. Probe evidence (develop, temp site, `--no-tls`, macOS sandbox)

`HOSTNAME` here = the laptop name (shown as `HOST`); it does **not resolve** in this sandbox
(`gethostbyname_ex` → `Errno 8`), so every `HOSTNAME`-advertised client fails — a live R6 repro.

| Drive | `-H` the client got | Outcome |
|---|---|---|
| `-H 127.0.0.1 --launcher rec.sh` | `127.0.0.1` | exit 0 in ~3 s |
| default `-H`, `--launcher rec.sh` | `HOST` | client `HostAddressInfo: Could not resolve host`; cluster still running at 8 s (R1) |
| `-H 127.0.0.1 --autoscaling fixed -P 2s --launcher rec.sh` | `HOST` ×5 in 10 s | each launch fails to resolve, relaunched every period (R3) |
| `-H 127.0.0.1 --ssh node-1`, stub runs locally | `client -H HOST -p 50001` in the remote script | client fails; cluster running at 12 s |
| same, stub exits 255 | `HOST` | cluster running at 12 s (R1/R2) |
| `--advertise x --launcher true` | — | `unrecognized arguments: --advertise x`, exit 2 |

Scripts (in `$TMPDIR/advprobe/`; reusable for verify):

```sh
# rec.sh — recording launcher: log argv to $REC_LOG, then run it
#!/bin/sh
log="${REC_LOG:-${TMPDIR:-/tmp}/rec.log}"
{ printf 'launch:'; for w in "$@"; do printf " '%s'" "$w"; done; printf '\n'; } >> "$log"
exec "$@"
```
```sh
# stub/ssh — first on PATH: log argv; STUB_SSH_RUN=1 runs the remote command here (stdin passed
# through, so the key arrives on stdin as in real ssh), else exit 255 like an unreachable host
#!/bin/sh
log="${REC_LOG:-${TMPDIR:-/tmp}/rec.log}"
{ printf 'ssh:'; for w in "$@"; do printf " '%s'" "$w"; done; printf '\n'; } >> "$log"
[ "${STUB_SSH_RUN:-0}" = 1 ] || exit 255
while [ $# -gt 1 ]; do case "$1" in -o|-p|-l|-i|-F) shift 2;; -*) shift;; *) shift; break;; esac; done
exec sh -c "$*"
```
```sh
# drive.sh LABEL SECONDS -- <hs cluster args>  (inside temp_site.sh; bounded; stops by recorded PID)
#!/bin/sh
label=$1; secs=$2; shift 3
export REC_LOG="$PWD/rec-$label.log"; : > "$REC_LOG"; seq 4 > t.in
uv run hs cluster t.in "$@" > out-$label.txt 2> err-$label.txt &
pid=$!; i=0
while kill -0 $pid 2>/dev/null && [ $i -lt $secs ]; do sleep 1; i=$((i+1)); done
if kill -0 $pid 2>/dev/null; then echo "[$label] still running after ${secs}s"; kill $pid; sleep 1
  kill -9 $pid 2>/dev/null; pkill -P $pid 2>/dev/null; wait $pid
else wait $pid; echo "[$label] exited $? in ~${i}s"; fi
grep -o "'-H' '[^']*'\|client -H [^ ]*" "$REC_LOG"; tail -4 err-$label.txt
```
Invocation: `export UV_CACHE_DIR=$TMPDIR/uv-cache; P=$TMPDIR/advprobe; .agents/factory/bin/temp_site.sh
env PATH="$P/stub:$PATH" STUB_SSH_RUN=1 sh -c "$P/drive.sh ssh 12 -- --no-tls -t 'echo {}' -H 127.0.0.1
--ssh node-1"`. Post-fix expectations: every mode's grep shows `127.0.0.1` and the run exits 0;
`--advertise localhost` shows `localhost`; `--advertise x` without a mode exits 2 naming `--advertise`.
The key never appears in the log (env / ssh stdin only).

## Recommendation

One helper `advertised_host(bind, advertise=None)` in `cluster/launch.py`; four call sites; relay passes
no override. `server.advertise = PARAM_UNSET` declared; `--advertise` default `None`; `ClusterApp`
resolves option > key and refuses only the option in local mode; constructors take `advertise=None` and
never read config. Docs per §4, with man pages left to the release rebuild.
Rejected: option default from the key (can't refuse only the option); `server.host` as the key
(GOAL clarification: its `localhost` default would be advertised); widening `-H localhost` differently
(R6 keeps it); adding `::` to the wildcard set (IPv6 non-goal; unreachable today).
