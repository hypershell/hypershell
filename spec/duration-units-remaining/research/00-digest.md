# 00 — Digest

Research for `duration-units-remaining` ran on the lean path's exceptions: the GOAL hands four
questions to `/hs-plan` (Clarifications → *Left for `/hs-plan`*), and the change touches two files on
the high-blast-radius list (`client.py`, `server.py`). The planner did it sequentially, by probe of
develop under Python 3.14 and 3.11, one brief per question group. No brief contradicts another; one
recommendation each:

| Question | Recommendation | Brief |
|---|---|---|
| How `--delay-start` is parsed | New `parse_delay` in `core/types.py`: one optional leading `-`, then the a7 matcher (factored out of `parse_time`, which keeps its behaviour), returning an unrounded `float`. | [01](01-delay-start.md) |
| How its error names the random form | `-W`'s message for the same text, plus one clause naming the random form written attached. | [01](01-delay-start.md) |
| Spellings only `float()` took (`+5`, `.5`, `1e3`, `inf`, `nan`, `1_000`) | Refused (R4 over R5; a7's review precedent). Foreman decision. | [01](01-delay-start.md) |
| R6 launched command line | `-w`/`-S` already `str(int)`; `-d` written as a plain decimal (no exponent) so old and new clients both read it. | [01](01-delay-start.md) |
| Where `hs relay` converts `autoscale.period` | In `RelayApp` (cached property, checked in `check_args`, `ArgumentError` naming the key), passed to `RelayThread`; `DEFAULT_SITE_PERIOD` reads the built-in default so import never parses user config. | [02](02-config-and-relay-period.md) |
| Any other path for a string to the autoscalers | None: `hs cluster`/`hs server` take it only through the option; library defaults read the built-in `default`. | [02](02-config-and-relay-period.md) |
| `-Q` fractions | Round up through `parse_time` like every other R1 option; numbers from config keep their meaning. | [03](03-poll-and-completions.md) |
| Completion candidates | zsh descriptions state the forms; candidates add unit forms; never a signed unit form; bash (no description field) shows forms through candidates. | [03](03-poll-and-completions.md) |

High-blast-radius files touched: `client.py` (`-w`, `-d`, `-S` declarations, import line, `APP_HELP`)
and `server.py` (`-w`, `-P`, `-Q` declarations, `APP_HELP`). No logic in either changes.
