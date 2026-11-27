# 02 — Drift between `2.8.6` and `origin/develop`, and the 2.8.7 pick

Scope: which touched lines differ between the `2.8.6` tag and `origin/develop` (`15587cf`), so the
squash commit cherry-picks cleanly after the units fix (`15587cf`).

## Files

| File | `2.8.6` → `develop` drift near the edit sites |
|------|------------------------------------------------|
| `src/hypershell/core/config.py` | `__all__` last line (`'find_available_ports'` gains a comma and two relay lines); `server.bundlemax` default; the whole `relay` default table; the relay block after the import-time `try` (from `RELAY_MARKER` on). **Identical:** imports (line 29), `blame`/`get_logging_style` (286–310), the `try`/`except` at 355–363. |
| `src/hypershell/config.py` (`hs config`) | **none** (byte-identical). |
| `src/hypershell/server.py` | config import line identical (65 vs 67) and its neighbors; class body `auth:` line identical (1417 vs 1622) with identical context. The units fix changes line 69 (`core.types` import), two lines below the config import. |
| `src/hypershell/submit.py` | config import identical (63 vs 65); class-body `queue_auth` block identical (938 vs 1304). Units fix: line 72 and the `-W` option, far away. |
| `src/hypershell/client.py` | config import identical (63 vs 64); `run()` key lines identical (1594–1598 vs 1664–1668). Units fix: line 68, far away. |
| `src/hypershell/core/queue.py` | config import identical, but the **next line** differs (`Logger` vs `Logger, close_inherited_slot_locks`): an edit to the import line would conflict. Not touched. |
| `src/hypershell/relay/**`, `tests/test_relay_*` | **absent** on `2.8.6`. |
| `pyproject.toml` | `cmdkit[toml]>=2.7.7` line identical; drift only in `version` (line 3) and the docs group (`sphinx-reredirects`). |
| `uv.lock` | `2.8.6` **has a `uv.lock`** (no other lock). The `cmdkit` package block (2.7.7, sdist, wheel, `toml` extra → `tomli`, `tomli-w`) and the `cmdkit` `requires-dist` specifier are identical on both; drift is the `hypershell` version, `sphinx-reredirects`, and `jinja2` 3.1.5 → 3.1.6, none adjacent. cmdkit 2.8.0 keeps the same `toml` extra. |
| `tests/__init__.py`, `tests/conftest.py` | `conftest.py` identical; `tests/__init__.py` gains helpers on develop, but `main`, `main_lines`, `temp_site`, `isolate_environment` are on both. |
| `docs/whats_new.rst` | differs (the *Not yet released* block): expected conflict, dropped on the pick. |
| `docs/security.rst` | differs (develop added relay text), not touched (GOAL ruling). |

## Pick plan

1. On the 2.8.7 branch cut from `2.8.6`: pick `15587cf` (the units fix) first, as its TECH says.
2. Pick this fix's squash commit. Expected conflicts, all resolved by dropping the path:
   `docs/whats_new.rst`, `spec/`, `src/hypershell/relay/upstream.py` (modify/delete),
   `tests/test_relay_launch.py` (modify/delete). Everything else applies clean.
3. On the 2.8 branch run `uv lock --check` and `uv run pytest tests/test_auth_variants.py`; the
   relay rows in the shared code find no `[relay]` table and do nothing.

## Gate

`git merge-tree --write-tree --name-only --no-messages --merge-base=<plan commit> 2.8.6 HEAD`,
failing on any conflicted path other than the four dropped ones, as the units fix's P2 did. Because
the units fix is picked first, the build also runs the same check once against a synthetic
`2.8.6 + 15587cf` commit (`git merge-tree --write-tree` then `git commit-tree`, no ref moved).
