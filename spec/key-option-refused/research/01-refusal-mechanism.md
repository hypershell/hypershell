# 01 — How to refuse the key option without echoing it

Question (GOAL Clarifications): cmdkit reports an unknown option with its value, so the option must
be recognised and refused. Which mechanism refuses every form the parser accepted before, lets a
task's own `-k` through, and keeps the key out of stderr and every log record?

Probes ran on the worktree's environment at `f81842c` (develop `2ffb5cc` plus the shaping commits):
cmdkit 2.8.0, CPython 3.15, in a throwaway site (`HYPERSHELL_SITE`, `HYPERSHELL_DATABASE_FILE`)
with `HYPERSHELL_LOGGING_LEVEL=DEVEL` and `HYPERSHELL_LOGGING_FILE_LEVEL=devel`, so a log file was
written at the most verbose level. The probe script patched the parsers in-process and was not
committed; its three modes and results follow. `K` is a 18-character dummy key.

## How the parser and the logging meet

- `hypershell.main()` calls `initialize_logging(role=…)` **before** `HyperShellApp.main(argv)`, so
  the stream handler and any configured file handler are live when the subcommand parses.
- `HyperShellApp` is a cmdkit `ApplicationGroup` with `ALLOW_PARSE` unset: the top-level parser sees
  only the first word; the rest goes verbatim to the subcommand's `Application.main`.
- cmdkit's `Interface.error()` raises `cmdkit.cli.ArgumentError(message)`; `Application.main` catches
  it and calls `log_critical(error)`, which `hypershell/__init__.py` points at the `hypershell`
  logger. The message therefore reaches stderr **and the log file**, and the command returns
  `exit_status.bad_argument` (2). Nothing else records the command line: no `sys.excepthook`, no
  argv logging at any level (grep of `src/`; the DEVEL log of every probe below holds only the
  logging set-up lines and the CRITICAL line).
- Argument parsing happens in `from_cmdline`, before `__enter__` and `run()`. A parse-time refusal
  therefore binds no port, dials nothing, submits nothing and opens no database: the probe site's
  `task.db` was never created.

## Mode 1 — today's parser (what each form binds)

| argv (after the command) | bound |
|---|---|
| `client -k K`, `-kK`, `--auth K`, `--auth=K`, `-k=K` | `auth = K` |
| `client --au K`, `client --aut=K` (unambiguous prefixes) | `auth = K` |
| `client --a K` | refused: `ambiguous option: --a could match --auth, --available-cores, --available-ssh-groups` |
| `server --au K` | refused: ambiguous with `--autoscaling` |
| `submit -qkK echo hi` (bundled short flags) | `queue_mode, queue_auth = K` |
| `submit echo hi -k K` (no `--`) | `queue_auth = K`, task `echo hi` |
| `submit -- echo -k K`, `submit echo -- -k K` | task `echo -k K`, `queue_auth = None` |

So "every form the parser accepted before" (R1) includes the unambiguous long prefixes (`--au`,
`--aut`, `--auth` on `client`/`submit`; `--auth` only on `server`), `-k=K`, and `-k` bundled after a
flag (`-qkK`). And `hs submit cmd -k K` without `--` has always taken `K` as **submit's** key, not
the task's: only `--` (or a task file) gives a task its `-k` (R3).

## Mode 2 — option deleted (the naive fix)

`hs client -k K`, `hs client --au K`, `hs submit -q echo hi -k K`, `hs server --auth=K -f x`: each
exits 2 with `CRITICAL [hypershell] unrecognized arguments: -k K` (or `--au K`, `--auth=K`), and the
key is in **every** role's log file (`client-…log`, `submit-…log`, `server-…log`). Confirms the
GOAL's finding and extends it: the leak reaches the configured log file, not only stderr.

## Mode 3 — option kept, bound to a refusing `argparse.Action` (chosen)

The `-k/--auth` action was replaced by an `argparse.Action` subclass whose `__call__` raises
`cmdkit.cli.ArgumentError(<fixed message>)`, with `nargs='?'` and `default=argparse.SUPPRESS`.

| argv | result |
|---|---|
| `client -k K`, `-kK`, `--auth K`, `--auth=K`, `--au K`, `-k=K`, bare `-k` | exit 2, refusal |
| `client --bogus -k K`, `client -k K --bogus` | exit 2, refusal (the action raises mid-parse, so argparse never reports the extras) |
| `client -k -N4` | exit 2, refusal (`nargs='?'` takes no option-like value) |
| `client -N notint -k K` | exit 2, `invalid int value: 'notint'` (an earlier error wins; no key) |
| `server -k K tasks.in`, `server tasks.in --auth=K --forever` | exit 2, refusal |
| `submit -qkK echo hi`, `submit echo hi -k K`, `submit --a=K` | exit 2, refusal |
| `submit -q -- echo -k K` | not refused: parsed as a task (`ConnectionRefusedError` only because no server ran) |

After all 18 runs, `grep -rl K <site>` found nothing: no stderr line and no log record at DEVEL held
the key, and no database file was created. argparse passes the action the matched option string
(`-k` or `--auth`, even for `--au`), never the value. cmdkit's `ArgumentError` is not
`argparse.ArgumentError`, so argparse's own `exit_on_error` wrapper does not intercept it (3.11–3.14
behave alike here).

## Residual echoes the mechanism does not cover (pre-existing, outside R1)

- **Ambiguous prefixes with `=`**: `hs server --au=K` and `hs client --a=K` fail today with
  `ambiguous option: --au=K could match --autoscaling, --auth` — argparse quotes the whole token.
  These forms were never accepted, so R1 does not reach them; they echo before and after this
  change.
- **Commands without a key option**: `hs relay -k K` and `hs cluster --auth=K` report
  `unrecognized arguments: … K`. (`hs cluster -k K` happens to print only `-k`, since `K` is taken as
  its positional file.) The top level (`hs -kK server`, `hs --auth=K server`) prints only `the
  following arguments are required: command`.
- Both are argparse/cmdkit echoing an unrecognised or ambiguous token; the general fix (redact the
  value in those messages) belongs in cmdkit's `Interface` and is a follow-up, not this cycle.

## Side observation

`hs client -t '-k {}'` fails today with `argument -t/--template: expected one argument`: argparse
reads a value that starts with a registered short option as that option. Keeping `-k` registered
keeps this exactly as it is (`--template=-kfoo` binds the template before and after).
