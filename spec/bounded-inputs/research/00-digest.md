# 00 — Digest: decisions from the research pass

Five briefs, fanned out on 2026-11-24 against `925e7f0d` (`src/`, `tests/`, `docs/`, `share/` as on
develop `03a02bd8`): [`01`](01-option-inventory.md) the option inventory and argparse mechanics,
[`02`](02-config-load.md) the configuration load, `hs config set` and the backstop,
[`03`](03-read-sites-and-no-bypass.md) every numeric read site and how to make "no bypass" checkable,
[`04`](04-tags-and-group-parser.md) resource tags, the shared group parser and the follow-up's needs,
[`05`](05-tests-docs-harness.md) tests, docs and the harness. The advisory tier (R13) was added to the
GOAL after the briefs were sent; its design is the plan's own. Where briefs differ, the ruling below
is the plan's.

## The split (C4): does not apply

Four phases carry the whole GOAL: tests first, then the table with every option, then configuration,
then tags and docs. Most criteria are one mechanism applied many times, and the configuration check
needs no read-site edits (decision 3). The advisory tier rides in the table and is emitted by the
same three routes, so it adds no phase. R9 and R10 stay here.

## Decisions

1. **Where the table lives.** `core/config_schema.py`, beside `KEYS`: a `Range` record per class
   (kind, floor, ceiling, the zero's meaning, advisory limit and its reason, justification) and
   `SETTINGS`, one entry per numeric key, per zone key (`relay.zone.*.<key>`) and per option-only
   setting. The duration and size grammar (`TIME_*`, `MEMORY_*`, `_match_seconds`) moves there too,
   so the module stays standard-library only and holds one parser per kind. `core/types.py` keeps
   `parse_time`, `parse_bytes`, `parse_delay`, `parse_period`, `parse_rate` as thin wrappers over the
   table (tags, templates and the API import them). `RATE_MINIMUM`, `GROUP_MIN`/`GROUP_MAX`, the evict
   floor and the handshake cap are read from table entries, never restated (01, 03, 04).
2. **How options share it.** A `RangedOption` callable in `core/types.py`, one per option, names its
   table entry (`.setting`) and the option's display name (its flags joined by `/`, a hidden alias
   such as `--num-tasks` left out). On refusal it raises cmdkit's `ArgumentError` with the R4 line, so
   argparse does not prefix `argument …:` and cmdkit prints it as written, exit 2 (01, prototyped).
   It is **not** a `ValueError`/`ArgumentTypeError` subclass; 05's suggestion is overruled for that
   reason. The marker is what the R1 test reads.
3. **Configuration: check once at load and write back** (03 over 02). The import-time load in
   `core/config.py` runs `check_settings()` on the merged configuration: each numeric key and zone key
   any layer gives (literal, `_env` or `_eval`) is resolved once, parsed by its kind and range, and the
   value written back into the layer that gave it, under the plain key, so `which()`/`blame` still
   name the file or variable. Whole numbers and numbers become numbers; a checked duration or size
   stays as written, because its readers round differently (`parse_time` up, `config_seconds` exact).
   Every later read (about 70 sites, class bodies, the relay, the autoscaler, launched clients, API
   defaults) then sees a checked value with no edit. 02's accessor-everywhere design was rejected: it
   touches every read site and still needs the load check, since C2 checks keys no command reads.
   From 02 the plan keeps: the `hs config` sniff (as `_relay_requested`), a raw view for `hs config`,
   the `reading()` backstop and the printable-value cutter.
4. **`_eval` runs once.** A numeric key's `_eval` is resolved at load and pinned; today it runs at
   every attribute read (four times per process). Recorded as a deviation (PLAN § 3).
5. **The `hs config` exemption.** Under `hs config` the check skips file values (environment values
   are still refused) and `hs config get`/`which` read an unchecked view, so a bad file shows and can
   be repaired. A refused file value is replaced by its built-in default in the checked view only,
   so the other modules' class bodies still import.
6. **Ranges changed under R1** (each recorded in TECH.md): threads' floor widens to 0, which is
   "all cores" today (`client.py:1362`, help, completions) (a); `-n/--interval`'s floor rises to 1 s,
   since 0 polls the database in a tight loop (c); `server.tls.handshake` gets its own entry, 1 … 86,400
   s, the cap `core/tls.py` already holds (b), its floor 1 as today. Not range changes, but behaviour
   the plan states: `-N/--num-threads` is named without `--num-tasks`; only `relay.min/max/init` take
   `<none>`, the rest of "count or none" are whole numbers from 0; `hs client -T 0`/`-W 0`, refused
   today, are in the table's Duration range and read as "none", as the keys and `hs cluster` read
   them.
7. **The size parser is exact.** `parse_bytes` computes with `Fraction`; today `int(float(…))` turns
   `9223372036854775807` into 2⁶³ and cannot reach the ceiling (05).
8. **Printable values.** `<V>` is always the value as given (option text, variable text, `_eval`
   output, the TOML value), cut to a short form: `repr` up to 32 characters, else head…tail with the
   length; an integer too wide to print is shown by `Decimal` in `.4e` form (02).
9. **Backstop (R8).** One context manager, `reading(stage)`, turns an escaping `ValueError` or
   `OverflowError` into an `ArgumentError` (exit 2) or a configuration refusal (exit 3), applied around
   argument parsing (a mixin on `from_cmdline`), the start checks up to the first line of work, the
   import-time load, and the app imports in `hypershell/__init__.py`. Errors raised during real work
   keep today's path (02).
10. **One message, no class prefix.** Option refusals print the R4 line as written; configuration
    refusals print it with ` (from: …)`, without the `ConfigurationError: ` prefix the shared handler
    adds today (02's open point).
11. **Tags (R9).** A base `TaskInputError` with `TaskGroupError` (name kept) and a new
    `TaskResourceError`; `ensure_resources()` beside `ensure_group`, at the same three points
    (`split_argline`, `check_tags`, the JSON record check). A JSON value is read as a configuration
    file's value of that kind. Retries and `Task.new`'s keyword arguments stay unchecked (04).
12. **One group parser with the parallel cycle.** `parse_group(text) -> int` keeps its name,
    signature and `TaskGroupError`; its body becomes the table's Group entry. `update-group-and-
    legacy-filters` (plan approved, `eec25fc0`) calls it from `hs update` and lands first; this cycle
    rebases and its call site needs no edit (04).
13. **Reuse by `update-typed-columns`.** Public `parse_setting(name, value)` and `Range.parse(value,
    name=…)` raise one `SettingError(ValueError)` with the R4 message, usable without argparse or a
    configuration load. Column-only values (`attempt`, `exit_status`) are not table entries; the
    follow-up builds its own `Range` with the same parser (04).
14. **Advisory tier (R13).** A `Range` may carry `advise=(above|below, limit, reason)`. One
    `advise()` reports past the limit at most once per setting per process; reports made before
    logging is set up (configuration at import, argument parsing) are queued and logged when the
    command starts. Tags give one summary per submission. Defaults are inside every advisory limit
    (an R1 test).
15. **No bypass, checkable.** Three gates: (a) after load, every numeric key holds a checked value
    and no numeric `_env`/`_eval` form remains; (b) every option that takes a value is a
    `RangedOption` or in a text allow-list compared by set equality, and none has type `int`/`float`;
    (c) a grep gate that the retired checks and constants are gone from `src/` (03, 05).

## Harness

Reuse `spec/submit-input-checks/drive/bounded.py` and `run_list.sh` and
`spec/relay-hardening/drive/red_check.sh`; new `drive/red.txt`, `drive/guards.txt` and
`drive/bounds_drive.sh`. The full R11 matrix runs in process against the parsers; the CLI covers one
setting per kind and route. The docs build needs the sandbox off (`sphinx_sitemap` binds a socket).
The full suite collects 4,671 tests; bound the serial run at 7,200 s (05).
