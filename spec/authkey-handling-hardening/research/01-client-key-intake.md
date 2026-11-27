# 01 — How launched clients take their key, across versions

Scope: what a client of each supported version does with its key when it is started with no
`-k`. This decides whether a launcher-side change can meet R4/R5 with no client change.
Read-only, codebase and tags only.

- **Every tag reads the key the same way.** At 2.8.0, 2.8.1, 2.8.2, 2.8.3, 2.8.4, 2.9.0a1 and
  2.9.0a4, `src/hypershell/client.py` declares `auth: str = config.server.auth` as the default of
  `-k/--auth`. `src/hypershell/core/config.py` merges `Environ(prefix='HYPERSHELL').expand()`
  into `config`, so `HYPERSHELL_SERVER_AUTH` becomes `config.server.auth`. That holds on every
  tag. (Method: `git show <tag>:path | grep`.)
- **Precedence.** CLI > `HYPERSHELL_*` env > local > user > system > defaults (AGENTS.md
  § Configuration). With no `-k`, the environment value outranks any `server.auth` in a config
  file on the client node, so the run's key always wins.
- **Config variants.** cmdkit's `Namespace.__getattr__` raises "more than one variant" if `auth`
  and `auth_env`/`auth_eval` coexist after the merge. The built-in default always defines `auth`,
  so a file-level `auth_env`/`auth_eval` already fails today. The environment handoff adds no new
  failure. (The pre-existing collision is noted in PLAN §5.)
- **Client side unchanged.** No client option, handshake or wire change is needed, so
  `client.py`, `server.py` and `core/queue.py` stay untouched (GOAL non-goals).
- **2.9 line floor.** A 2.9 server refuses clients older than the work-request protocol
  (#78, 2.9.0a4), so on `develop` R5 is pinned against 2.9.0a4's `hs client` option set. On the
  2.8.5 backport the floor is 2.8.0 (PLAN §7).
