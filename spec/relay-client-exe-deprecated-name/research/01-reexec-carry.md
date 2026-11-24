# 01 — The relay's own program, and how it survives the re-execution

Scope: GOAL Q2 (ruled option (c)) hands the mechanism to `hs-plan`. Read on `develop` `11faef5e`.

## Where `sys.argv[0]` comes from, per start form

Checked on macOS with Python 3.14 and a uv venv (2026-11-23). The `hs` console script uv writes has
an absolute shebang (`#!/…/venv/bin/python`) and only strips `-script.pyw`/`.exe` from `argv[0]`.

| Start form | `sys.argv[0]` in the relay | Re-executes? |
| --- | --- | --- |
| `hs relay` found on `PATH` (a `bin/hs` symlink) | the path the shell found, symlink unresolved | no (`_relay_requested`) |
| `./bin/hs relay` | `./bin/hs` (relative) | no |
| `hyper-shell relay` | the `hyper-shell` path | no |
| any of these with `--database PATH` naming another file | `-c` after `bind_database`'s `execve` | yes |
| `main(['relay', …])` from Python | the embedding program (`pytest`, a user script) | yes (not `RELAY_PROCESS`) |
| test harness `python tmp/hs relay` (`tests/test_relay_db.py` `RECORDER`, `main(sys.argv[1:])`) | `tmp/hs`, a non-executable file with no shebang | only with `--database` |

`core/config.py::_relay_requested` reads `sys.argv` at import; `RelayApp.main` keeps the relay's own
arguments in `RelayApp.argv` (`relay/__init__.py:1148`); `bind_database` (`:1356-1377`) re-executes
as `[sys.executable, '-c', REEXEC_CODE, 'relay', *self.argv]` with `os.environ` plus
`HYPERSHELL_RELAY_DATABASE`, `RELAY_MARKER` and `REEXEC_MARKER`. Nothing else crosses.

## Two traps in the ruled rule

1. **Embedded `main(['relay', …])`.** `sys.argv[0]` names an existing file that is not HyperShell.
   Taken literally, option (c) would launch site clients as `/…/pytest client …`. The process's
   command line is the relay's only when `sys.argv[1:] == ['relay', *RelayApp.argv]`; that is true
   for `hs relay`, `hyper-shell relay`, a wrapper that `exec`s the real `hs`, and the `RECORDER`
   harness, and false for an embedding program. Use `sys.argv[0]` only then.
2. **A file that cannot be run.** The `RECORDER` harness's `argv[0]` exists but is not executable.
   Requiring `os.access(path, os.X_OK)` as well as `os.path.isfile` drops it to the next candidate
   (the venv's `hs`), which is what those tests can run. A user's `python script.py relay` falls the
   same way.

Both are refinements of "names an existing file", inside "the mechanism is the plan's"; the plan
gate should confirm them (PLAN §5).

## Carrying the program across `execve`

Options considered:

- **A private environment variable** beside `REEXEC_MARKER`: `_HYPERSHELL_RELAY_PROGRAM`, set in the
  `execve` environment, popped by the re-executed relay and trusted only when `REEXEC_MARKER` is
  also present. Mirrors the two existing markers; invisible in `ps`; never reaches a site client,
  because it is popped from `os.environ` before any launch (the fence's prefix `HYPERSHELL_RELAY_`
  would not catch the leading underscore, so the pop is what keeps it out — tested).
- **Append `--remote-exe PROGRAM` to the re-executed argv.** Rejected: it would outrank
  `relay.remote_exe` (R3's precedence is option over key over default), and it shows a value the
  user never gave in `ps` and in the argv the re-executed relay logs.
- **Rewrite `argv[0]` in `REEXEC_CODE`** (`sys.argv[0] = sys.argv.pop(1)`). Rejected: the child would
  then resolve again, and a fallback result (`hs` bare) would be re-read as a relative file path in
  the child's working directory.

**Chosen: the private variable.** The resolution runs once, in the process the user started, before
`bind_database`; the re-executed process takes the carried value as resolved.

## Proven in a scratch prototype (2026-11-23)

A copy of `src/hypershell` patched as PLAN §2 describes, run through `PYTHONPATH`, with a
`bin/` holding only `hs` and `hsx` symlinks and `PATH=bin:/usr/bin:/bin`:

- `develop` as is: the relay logs `Autoscale launcher (zone default): hyper-shell client …`, then
  `CRITICAL … FileNotFoundError: … 'hyper-shell'` from the `SiteScaler`; the three tasks remain.
- prototype, plain: `…/bin/hs client …`; all tasks complete.
- prototype, `--database PATH` (logs `Re-executing to bind relay database`): `…/bin/hs client …`;
  all tasks complete.
- prototype, `--database` with a launcher that counts `_HYPERSHELL_RELAY*` names in its environment: 0.
- `HYPERSHELL_RELAY_REMOTE_EXE=/x` → launched as `/x`; with `--remote-exe /y` too → `/y`;
  `HYPERSHELL_RELAY_REMOTE_EXE=` (empty) → the relay's own `bin/hs`.
