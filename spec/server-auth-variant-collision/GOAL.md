# GOAL — `auth_env` and `auth_eval` in a config file break every command

> **Origin spec.** The *what* and *why* — the locked contract `hs-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `hs-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** server-auth-variant-collision
- **kind:** fix
- **appetite:** small

## Problem

`docs/security.rst` tells users to keep the authkey out of the config file with the `_eval` / `_env`
suffixes (`docs/security.rst:258-260`, the suffix note at `:649-656`, the worked examples at `:641`
and `:672`, and for the relay's upstream key at `:523` and `:551`). Following that advice breaks
HyperShell. The built-in defaults give `server.auth` and `relay.upstream.auth` the placeholder
`DEFAULT_AUTH` (`src/hypershell/core/config.py:80`, `:134`, `:211`). With the current floor,
`cmdkit[toml]>=2.7.7` (`pyproject.toml:31`, locked at 2.7.7), a user file that adds `auth_env` or
`auth_eval` beside that default makes every read of the key raise
`AttributeError: 'auth' has more than one variant`. The first read happens at import, so no command
runs, not even `hs config get`: the user gets a raw traceback and exit status 1. **Pre-existing:**
the default `auth` and the documented suffixes are both in 2.8.0; the seed reproduced it on 2.9.0a4.

cmdkit 2.8.0 (released 2026-11-11) treats `name`, `name_env` and `name_eval` as one key across a
`Configuration`'s layers, the highest layer deciding. An external run of `develop` against 2.8.0
found no regressions. Probing `develop` (`50e64a9`) under cmdkit 2.8.0 on 2026-11-11, with a file
named by `HYPERSHELL_CONFIG_FILE`, shows what that upgrade alone still leaves broken:

- `[server] auth_env = "VAR"` with `VAR` set: the import-time read now yields the variable's value,
  but `hs config get server.auth` prints `ConfigurationError: "server.auth" not found in
  configuration` and exits 3. `[relay.upstream] auth_env` behaves the same for
  `hs config get relay.upstream.auth`.
- `VAR` **unset**: `config.server.auth` (and `config.relay.upstream.auth`) silently becomes `None`,
  with no error. Nothing tells the user the key is missing.
- `auth_eval` naming a command that fails: a raw `subprocess.CalledProcessError` traceback.
- `auth` and `auth_env` in the **same** file: still the raw `AttributeError` traceback.

## Outcome / vision

A user can set the authkey with `auth_env` or `auth_eval` in any configuration file, as the security
guide says, and every command uses the resolved value, which takes precedence over the built-in
default. `hs config get` reports it like any other key. A variable that is unset, a command that
fails, or a file that gives the key twice is a clear HyperShell configuration error, never a silent
`None` or the default key, and never a traceback.

## Acceptance criteria (the contract)

- **R1** — WHEN a configuration file sets `[server] auth_env` naming a set variable, or
  `[server] auth_eval` naming a command that succeeds, every command SHALL run without a
  configuration error and SHALL use the resolved value as `server.auth`, in place of the built-in
  default.
- **R2** — WHEN a configuration file sets `server.auth_env` or `server.auth_eval` and the value
  resolves, `hs config get server.auth` SHALL print the resolved value and exit 0.
- **R3** — WHEN one configuration layer sets `server.auth` and a higher layer sets `auth_env` or
  `auth_eval` (or the reverse), the higher layer's form SHALL decide the value commands use and
  `hs config get server.auth` prints.
- **R4** — Where the configuration has a `[relay.upstream]` section, R1, R2 and R5 SHALL hold for
  `relay.upstream.auth` as they do for `server.auth`.
- **R5** — IF `auth_env` names a variable that is unset, or `auth_eval` names a command that exits
  non-zero, THEN `hs config get` of that key and every command that uses the key SHALL exit
  non-zero with a HyperShell configuration error naming the key, with no traceback, and SHALL NOT
  proceed with `DEFAULT_AUTH`, `None`, or any other substitute.
- **R6** — IF one configuration layer sets more than one of `auth`, `auth_env` and `auth_eval`,
  THEN commands SHALL exit non-zero with a HyperShell configuration error naming the key, not a
  traceback.
- **R7** — HyperShell SHALL require `cmdkit[toml]>=2.8.0` (`pyproject.toml` and `uv.lock`), on
  `develop` and in the 2.8.7 patch alike.

## Non-goals (no-gos)

- The environment-variable form (`HYPERSHELL_SERVER_AUTH_ENV` and kin). Under cmdkit 2.8.0 it still
  turns `server.auth` into a table (`Namespace({'env': …})`); that is the ROADMAP entry
  *Environment variables cannot reach every configuration key*, not this fix.
- Unset-variable and failed-command errors for keys other than `server.auth` and
  `relay.upstream.auth`. cmdkit 2.8.0 already fixes the precedence collision for every key; the
  guard here covers the two authkeys only.
- Masking or redacting secrets in `hs config get` output (see Clarifications).
- A variable that is set but empty: it resolves to an empty key, and the existing key policy
  (`hs server` refuses keys under 16 characters) handles it unchanged.
- Changes to cmdkit itself, and any new configuration syntax.
- Rewording `docs/security.rst`, which this fix makes true (ruling (b) below).

## Clarifications

- **Q:** Which releases carry this fix, and what does that constrain? — **A:** It ships in the
  2.8.7 patch (cherry-picked onto the `2.8.6` tag) and in 2.9.0a7. It must be pick-safe onto
  `2.8.6`, and the 2.8 line must also require `cmdkit[toml]>=2.8.0`. `2.8.6` has no `[relay]`
  section, so R4 is verified on the 2.9 line only and its changes must stay separable from the rest
  on the pick. (maintainer via foreman, resolved 2026-11-11)
- **Q:** Do the docs change? — **A:** `docs/security.rst` recommends `auth_env` / `auth_eval`; it
  stays as written unless the fix makes it wrong. (maintainer via foreman, resolved 2026-11-11)
- **Q:** Does `hs config get server.auth` print a resolved `auth_env` / `auth_eval` secret (the
  seed's open question)? — **A:** Yes: it reports the resolved value, as it does a plain
  `server.auth`. The docs promise only to keep the secret out of the file
  (`docs/security.rst:258-260`), not out of `hs config get`; the cycle brief asks that
  `hs config get server.auth` report the right value; masking is a non-goal. (foreman brief,
  resolved 2026-11-11)
- **Q:** Where to resolve variants: drop `auth` from the defaults, or resolve per layer (the seed's
  other open question)? — **A:** Settled upstream: cmdkit 2.8.0 resolves them as one key across
  layers. What remains is HyperShell's to design in `/hs-plan`. (resolved 2026-11-11)
- **Q:** What does cmdkit 2.8.0 do with an unset `_env` variable? — **A:** Probed 2026-11-11
  (`uv run --no-project --with 'cmdkit[toml]==2.8.0'`, a default layer with `server.auth` and a
  user layer with `server.auth_env = "PROBE_KEY"`): `auth_env unset -> None`, `auth_env set ->
  'from-env'`, `auth_env empty -> ''`; `auth_eval` of `false` raised
  `CalledProcessError Command 'false' returned non-zero exit status 1.`; `auth` with `auth_env` in
  one layer raised `AttributeError 'auth' has more than one variant`. cmdkit does not error on an
  unset variable, so R5 needs a HyperShell guard, not only a test.

## Related materials

- Seed: `issues/server-auth-variant-collision.md`
- cmdkit 2.8.0 on PyPI (cmdkit `master` at `a3cb454`): one key across layers for `name` /
  `name_env` / `name_eval`.
- `src/hypershell/core/config.py:80`, `:134`, `:211` — `DEFAULT_AUTH` and the two defaulted keys.
- `src/hypershell/submit.py`, `client.py`, `server.py` — class-body reads of `config.server.auth`
  at import.
- `docs/security.rst:258-260`, `:523`, `:551`, `:641`, `:649-656`, `:672`.
- ROADMAP: *Environment variables cannot reach every configuration key* (the env-var form, out of
  scope here).
