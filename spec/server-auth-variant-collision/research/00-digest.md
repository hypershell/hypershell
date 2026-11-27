# 00 — Digest

Two briefs, written by the planner (scoped fan-out: the GOAL defers the HyperShell-side design to
`/hs-plan`, and the change touches `server.py` and `client.py`).

1. **Precedence is cmdkit's; the guard is HyperShell's.** cmdkit 2.8.0 already lets the highest
   layer decide among `auth`/`auth_env`/`auth_eval` (R3). HyperShell adds one resolver,
   `core/config.py: resolve_auth(*section, base=None)`, that turns an unset variable (`None`), a
   failed command (`CalledProcessError`) and two forms in one layer into `ConfigurationError` naming
   the key ([01](01-key-reads-and-cmdkit.md)).
2. **Resolve at run, not at import.** The two class-body reads (`server.py`, `submit.py`) become
   `None` defaults resolved in `run()`, as `client.py` already does, so a bad key fails only the
   commands that use it, through the apps' existing `ConfigurationError → bad_config` mapping.
3. **Two forms are refused at import.** A per-layer scan (no environment or subprocess access) runs
   inside the existing import-time `try`, with a new `except ConfigurationError` branch that prints
   one line and exits `bad_config`. A lower layer's duplicate is invisible in the merged view, so
   only a scan satisfies R6 literally.
4. **`hs config get`** routes the two authkeys, merged view only, through the resolver (R2, R4, R5).
5. **Pick-safety** ([02](02-pick-onto-2.8.6.md)): every shared edit sits in lines identical on
   `2.8.6`; `core/queue.py` is left alone; relay-only code and tests live in files absent on
   `2.8.6`, dropped on the pick. `2.8.6` has a `uv.lock` whose `cmdkit` lines match develop's, so
   `uv lock --upgrade-package cmdkit` produces a hunk that picks clean.
