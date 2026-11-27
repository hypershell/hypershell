# 00 — Digest

Three briefs, fanned out because the change touches high-blast-radius files (`data/model.py`,
`server.py`, `client.py`) and GOAL hands five questions to `/hs-plan`. Cross-brief decisions:

1. **Config values** ([01](01-config-and-call-paths.md)). A string in `[task] memory/timeout` or
   `[client] memory/timeout` (or its environment variable) is an argparse string default, so argparse
   runs it through the option's `type=` already. Swapping the `type=` covers config with no
   config-model edit. Numbers (TOML int/float, numeric env values) skip `type=` as today.
2. **Error text** ([01](01-config-and-call-paths.md)). argparse drops a `ValueError`'s message; only
   `ArgumentTypeError` keeps it. One exception class subclassing both serves the CLI (`type=`) and the
   `ValueError` catchers in `data/model.py` and `core/logging.py`.
3. **Parsers return `int`** ([01](01-config-and-call-paths.md)): `client_options` forwards `str(value)`
   to launched clients, so an int round-trips as a bare integer.
4. **R4 needs the tag validator** ([01](01-config-and-call-paths.md), [02](02-inline-tags-and-submit.md)).
   `ensure_valid_tag`'s charset `^[A-Za-z0-9_.+-]+$` refuses `timeout:1:30:00` (colon) before
   `Task.new` runs. Decision: for the keys `memory` and `timeout` only, a string value is checked by
   its parser instead of the charset. Same text on 2.8.6; merges cleanly.
5. **Task.new edit site** ([02](02-inline-tags-and-submit.md), [03](03-docs-and-release-markers.md)).
   Any edit to or after the `other['timeout'] = tag.pop(...)` line conflicts on 2.8.6 (adjacent to
   develop-only fingerprint code). Normalise `tag['timeout']` in lines inserted above it and leave that
   line byte-identical; the memory line and the import line may change (merge-file simulation: clean).
6. **Bad inline value** ([02](02-inline-tags-and-submit.md)). Keep today's behaviour: earlier tasks
   stay, the command exits 5 with one `RuntimeError` line naming the value and the forms. No rollback
   (that is `issues/ingest-robustness.md`).
7. **Markers and notes** ([03](03-docs-and-release-markers.md)). No `versionchanged` marker (the #92
   precedent; a `2.9.0` marker would be false once 2.8.7 ships). One *Not yet released → Fixes* entry
   in `docs/whats_new.rst` ending "This also ships in 2.8.7."; master has no `whats_new.rst`, so the
   R9 sentence also goes in the squash commit body for the 2.8.7 GitHub release notes.
8. **Help snippets are hand-maintained**; man pages are rebuilt at release (don't touch); zsh
   completion descriptions "Memory in bytes"/"in seconds" are updated in the same commit.
9. **Rotation policy (R7)** needs no `core/logging.py` edit: a full-match `parse_bytes` refuses
   `30 2 * * *`, so the existing `except ValueError` branch makes it time-based.

Out of scope, worth seeds: `hs update` stores `timeout=1h` text in an INTEGER column; a retry drops the
parent's `memory`/`timeout`/`cores`; `[relay] timeout` is forwarded as text; a TOML float timeout is
forwarded as `5400.0`.
