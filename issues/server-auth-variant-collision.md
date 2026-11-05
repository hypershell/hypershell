---
status: unshaped
kind: fix
appetite: small
lane: public
---

# `auth_env` and `auth_eval` in a config file break every command

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

`docs/security.rst` tells users to keep the authkey out of the config file with the `_eval` / `_env`
suffixes: "prefer the `_eval` / `_env` suffixes (see below) to keep the secret out of the file"
(`docs/security.rst:179-181`), with `auth_eval` in both worked examples (`:506`, `:537`) and
`auth_env` named in the note on the suffixes (`:514-521`). Following that advice breaks HyperShell.

The built-in defaults define `server.auth` (`src/hypershell/core/config.py:132`, set to
`DEFAULT_AUTH`, `:78`). cmdkit's attribute lookup collects the plain key and its `_env` / `_eval`
variants from the merged configuration and refuses when more than one is present
(`cmdkit/namespace.py:85-95`, cmdkit 2.7.7, the floor `pyproject.toml:31` pins). A user file adds
`auth_env` or `auth_eval` beside the default `auth`, so every read of `config.server.auth` raises
`AttributeError: 'auth' has more than one variant`.

The first read happens at import: `SubmitApp` reads it in its class body
(`src/hypershell/submit.py:1304`), and the package imports `submit` (`src/hypershell/__init__.py:26`).
`ClientApp` and `ServerApp` read it the same way (`src/hypershell/client.py:1551`,
`src/hypershell/server.py:1549`). So no command runs, not even `hs config get`, and the user gets a
raw traceback with exit status 1 rather than HyperShell's configuration error.

Reproduced on `origin/develop` (`02b292a`, 2.9.0a4), with a temporary file holding only
`[server]` / `auth_env = "SOME_VAR"` named by `HYPERSHELL_CONFIG_FILE`, and the variable set to a
placeholder: `hs config get server.auth` exits 1 with the traceback above. `auth_eval = "echo …"`
fails the same way. Without the file the same command prints the default.

The same lookup also means `auth_env` naming an unset variable yields `None`
(`cmdkit/namespace.py:97-99`), not an error. A fix that lets the variant win must not let a missing
variable fall through to `None` or to `DEFAULT_AUTH`.

**Untested:** the `HYPERSHELL_SERVER_AUTH_ENV` environment form, and other keys with a built-in
default (`docs/_include/config_intro.rst:61-64` says the suffixes work on any option). By the code
path above, any defaulted key given an `_env` or `_eval` variant should fail the same way;
`database.password`, the docs' usual example, has no default and works.

## Why it was deferred

Found out of cycle (2026-11-05), not by a cycle's review, so no pass had it in scope.
**Pre-existing:** the default `auth` and the documented suffixes are both in 2.8.0.

## Outcome / vision

A user can set the authkey with `auth_env` or `auth_eval` in any configuration file, as the security
guide says, and every command uses the resolved value. A variant given in a file or the environment
takes precedence over the built-in default. A missing variable or a failing command is a clear
configuration error, never a silent fallback to the default key.

## Sketch of the acceptance criteria

- **R1** — WHEN a configuration file sets `server.auth_env` or `server.auth_eval`, every command
  SHALL resolve `server.auth` to that value, and `hs config get server.auth` SHALL print it.
- **R2** — IF `auth_env` names an unset variable or `auth_eval` fails, THEN the command SHALL exit
  with a configuration error naming the key, and SHALL NOT use `DEFAULT_AUTH`.
- **R3** — IF one configuration layer sets more than one of `auth`, `auth_env` and `auth_eval`,
  THEN the command SHALL exit with a configuration error naming the key and the file, not a
  traceback.
- **R4** — The same precedence SHALL hold for every key with a built-in default, or the docs SHALL
  say which keys accept the suffixes.

## Open questions for shaping

- **Where to resolve.** Drop `auth` from the default namespace and fall back in code, or resolve
  `_env` / `_eval` per layer before the layers merge, so a higher layer's variant replaces a lower
  layer's plain key. The second fixes every key at once.
- **Does `hs config get` print the resolved secret?** `docs/security.rst:179-181` warns that a
  plain `server.auth` is printed verbatim; printing a resolved `auth_eval` would defeat its purpose.

## Notes

- Related: `docs/security.rst` § the `_eval` and `_env` note, which the fix makes true.
- Found by: out-of-cycle review, 2026-11-05; reproduced on 2.9.0a4.
