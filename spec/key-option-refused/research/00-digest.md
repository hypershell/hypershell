# 00 — Digest

Two questions were handed to `hs-plan` by the GOAL; one brief each, done in-session (small appetite).
The coupled-core files `client.py` and `server.py` are touched only in their `Application` classes
(option wiring, help text, the key read in `run()`); no thread, FSM or queue code changes.

1. **Refusal mechanism** ([01](01-refusal-mechanism.md)). Keep `-k`/`--auth` registered on the three
   parsers, bound to a shared `argparse.Action` that raises `cmdkit.cli.ArgumentError` with a fixed
   message naming `HYPERSHELL_SERVER_AUTH` and `server.auth` (`nargs='?'`,
   `default=argparse.SUPPRESS`). Probed: every form the parser accepted before (`-k K`, `-kK`,
   `-k=K`, `--auth K`, `--auth=K`, unambiguous prefixes, `-qkK`, a trailing `-k K` after task words)
   exits 2 with the refusal, and no stderr line or DEVEL-level log record holds the key; `--` and
   task files are untouched (argparse never calls the action for them). Deleting the option instead
   echoes the key to stderr **and the log file**, since logging is initialised before parsing.
   Residual, pre-existing, outside R1: argparse's `ambiguous option: --au=K` and other commands'
   `unrecognized arguments: … K` quote the token; the general fix is a cmdkit follow-up.
2. **`cluster/launch.py` redaction** ([02](02-launch-redaction.md)). Keep it unchanged. HyperShell
   emits no `-k`; the guard only ever matches a launcher's own `-k` (`srun -k`, `ssh -k`) and then
   masks the next word in the log, a pre-existing cosmetic flaw that hides nothing secret. Changing
   it would alter every launch log line for no R-ID.
