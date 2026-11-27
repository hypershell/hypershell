# 02 — Does `cluster/launch.py` keep its `-k`/`--auth` redaction?

Question (GOAL Non-goals): whether `ClientLaunch.describe()` keeps redacting the word after `-k` or
`--auth` in a logged launch command, "so long as no log ever holds a key".

## What is there

- `ClientLaunch.describe()` (`cluster/launch.py:123-136`) is the only thing a launch logs. It first
  replaces any word that assigns `HYPERSHELL_SERVER_AUTH` (`assigns_key`, shell-parsed) with
  `HYPERSHELL_SERVER_AUTH=***`, then passes the words to `redact_secrets(argv, ['-k', '--auth'])`,
  which replaces the word after an exact `-k` or `--auth` with `***`.
- `redact_secrets` is public: exported from `cluster/launch.py` and re-exported by
  `cluster/remote.py` (`__all__`), pinned by `tests/test_cluster_launch.py::
  test_redact_secrets_still_available_from_remote`; its docstring example names `-k`/`--auth`.
- `client_launch()` never emits `-k` (invariants §11; `KEY_OPTIONS` checks in
  `tests/test_cluster_launch.py` and `tests/test_relay_launch.py`). Words in a launched argv come from
  `client_launch()` itself, the launcher (`--launcher`, `[ssh] args`, `--mpi`) and the template.

## What the redaction does today

It can only ever match a word the user put in the launcher or SSH options, since HyperShell emits no
`-k`. There, `-k` belongs to the launcher: `srun -k` (`--no-kill`) and `ssh -k` (no GSSAPI
delegation) take no value, so the log shows the next word (a launcher option or the host) as `***`.
That is a cosmetic flaw in a log line; it hides nothing secret and leaks nothing.

## Options

1. **Keep it (chosen).** No code, test or log change; R4 ("the clients `hs cluster` launches … join
   as before") is untouched by construction. The guard stays as defence in depth for an argv that
   some future change might build wrongly, and the `assigns_key` redaction, which is the guard that
   matters for the real hand-off, is unaffected either way. It offers nothing to a reader: it is a
   rule that names `-k` only to hide a value, which the GOAL's Non-goals already set aside for the
   R5–R8 sweep.
2. Drop `['-k', '--auth']` from `describe()` and keep `redact_secrets` for its public callers. Fixes
   the `srun -k`/`ssh -k` cosmetic mis-redaction, but changes the logged launch line in every mode
   for a cycle whose contract is the three commands' option, and removes a guard the GOAL asks to
   keep only "so long as no log ever holds a key".
3. Drop `redact_secrets` entirely. Breaks a public re-export pinned by a test; rejected.

Decision: option 1. The `srun -k`/`ssh -k` mis-redaction is pre-existing and independent of this
change; it can ride a later cluster cycle if anyone cares.
