# 01 — `hs config set` typing (R1–R4)

Research for `hs-plan`, cycle `config-set-and-unread`, on `d9901cf0`. Every claim below was probed in a
throwaway site; the proposed design was prototyped on a scratch copy of `src` (not in this tree) and
the proposed test file passed 63/63 on it (38 of them fail on `d9901cf0`).

## 1. Finding a path's kind: new `match_path(path) -> Kind`

**Recommendation:** add `match_path` to `core/config_schema.py` beside `match_name` (`:271`), and
export it. Do not feed `match_name` a synthesized name: it upper-cases (`:280`), so `Server.eager`
would match BOOL against the Non-goal; it lower-cases open/closed entries and zone names (`:301,305,306`);
`_`/`.` are confusable (`relay.zone.gpu_launcher` reads as zone `gpu`); and `relay.zone.gpu.foo` raises
VariableError (`:302`). A round-trip check could patch this, but is fragile.

```python
def match_path(path: str) -> Kind:
    """The kind a value written at dotted `path` is read as, the path matched as written."""
    if path in KEYS:
        return KEYS[path]
    for variant in VARIANTS:
        if path.endswith(variant) and path[:-len(variant)] in KEYS:
            return Kind.TEXT
    *table, entry = path.split('.')
    if TABLES.get('.'.join(table)) is TableKind.EXPORT or '.'.join(table) == 'ssh.nodelist':
        return Kind.TEXT
    if len(table) > 1 and TABLES.get('.'.join(table[:-1])) is TableKind.ZONE:
        return ZONE_KEYS.get(entry, Kind.UNTYPED)
    return Kind.UNTYPED
```

The conversion is the same `read_value` (`:219`) the environment layer uses (`:341`), so set and env agree.
How each case resolves:

| path | kind |
|---|---|
| documented key (`server.port`, `logging.file`, `ssh.nodelist`, `relay.min`) | `KEYS[path]` (INT, LOGGING_FILE, TEXT, INT_OR_NONE) |
| `server.auth_env`, `database.password_eval`, `server.port_env` | TEXT (like `match_name:286-289`) |
| `export.RUN_ID` (name as written) | TEXT |
| `ssh.nodelist.gpu` | TEXT |
| `relay.zone.gpu.launcher` / `launcher_args` | TEXT; `size/min/max/init` INT (`ZONE_KEYS`, not INT_OR_NONE) |
| `relay.zone.gpu.color` (unknown zone key), `relay.zone.gpu` | UNTYPED (no error: no warnings for unknown paths) |
| `database.connect_args.*`, `database.pragmas.*`, `database.sslmode` | UNTYPED |
| `server.nothing`, `logging.file.foo`, `foo.bar`, `Server.eager`, `relay.zone.GPU.SIZE` | UNTYPED |
| `server.port.x` (below a key), `export.A.B`, `ssh.nodelist.a.b` (deeper than one entry) | UNTYPED |
| `database` (one word) | refused before the lookup, "Missing section in variable path" (`config.py:299-300`) |

Edge cases: a zone key's variant (`relay.zone.gpu.launcher_eval`) is UNTYPED, since zone keys are not in
`KEYS` and R1 names only variants of documented keys (the env layer cannot name it at all). `server.`
or `a..b` behave as today (a `""` entry) — Non-goal, no new path rules. `server.port.x` replaces an
existing scalar `port` with a table, and the reverse, as today ("merge unchanged").

## 2. Refusal: exit 2 by `cmdkit.cli.ArgumentError`

- `Application.main` catches `ArgumentError` before the `exceptions` map and returns
  `exit_status.bad_argument` (2), logging the message only (cmdkit `app.py`, `main`). Probed:
  `hs config set a b --local` → rc 2, `CRITICAL [hypershell] Missing section in variable path`.
- The shared map sends any other exception to `write_traceback` with status 5
  (`core/exceptions.py:142`); that is today's `null` path (tomlkit `ConvertError`, rc 5, traceback file).
- **Recommendation:** drop `type=smart_coerce` (`config.py:284`, `value` stays a plain str), and in
  `ConfigSetApp.run` (`:296`), after the one-word check and before building the dict / `update()` (`:312`):

```python
try:
    value = read_value(match_path(self.varpath), self.value)
except ValueError as error:
    raise ArgumentError(f'{self.varpath}: expected {error}, given {self.value!r}') from None
```

  and write `value` (not `self.value`). Converting in `run()` (not as an argparse `type=`) is required to
  know the path. Messages, probed on the prototype (rc 2, no traceback, no file, no backup):
  `server.port: expected a whole number, given 'abc'`;
  `autoscale.factor: expected a number, given 'inf'`;
  `relay.min: expected a whole number or <none>, given 'none'`;
  `server.eager: expected a boolean (true/false, yes/no, on/off, 1/0), given ''`;
  `logging.file: expected true, enabled, or a path, given 'false'`.
  It is `read_environment`'s format (`config_schema.py:343`) minus `(from: NAME)`.
- Builder trap: `ConfigGetApp.run` has the identical line `section, *subsections, variable =
  self.varpath.split('.')` (`config.py:188`); anchor the edit inside `ConfigSetApp`.

## 3. Writing and "nothing is written"

- tomlkit through `update()`/`_inplace_update` (`core/config.py:680-711`) writes every `read_value`
  result correctly: `1_000`→`1000`, `-5`, `1e3`→`1000.0`, `0.5`, `false`, `"10m"`, `"<none>"`, `""`,
  escaped quotes/newlines, UNTYPED `0x1F`→`31`, `inf`/`nan` (TOML floats, R4). `read_value` never
  returns None, so the `ConvertError` path disappears.
- `update()` makes the backup (`:684-689`), or writes a header-only new file (`:690-692`), **before**
  parsing and assigning. Probed today: a refused `null` leaves a new `.config.<ts>.toml` and an
  `exception-*.log`. Refusing before `update()` leaves file, backups and Logs untouched (prototype:
  no `config.toml` created in a fresh site). No change to `update()` needed.
- Empty VALUE: TEXT `''`→`""`; UNTYPED `''`→`""` (`read_untyped`, no TOML token); BOOL/INT/NUMBER/
  INT_OR_NONE refuse. **Gap:** `read_value(LOGGING_FILE, '')` returns `''` (`:231-235`), but R2 wants it
  refused. **Recommend** fixing it in `read_value` with `text != ''` on the path branch: the env layer
  never reaches it (`logging.file` is in `EMPTY_UNSET`, skipped at `:336`), so env behaviour is
  unchanged; don't use `not word`, which would also start refusing a whitespace env value (exit 3).
  DURATION/SIZE `''` stays `""` (R3, checked by the reader).

## 4. EMPTY_UNSET vs R1

No contract conflict. `EMPTY_UNSET` is a layering rule: an empty variable is "not given", so it doesn't
override lower layers. R1 is explicit that a text key gets `""` as given, so `hs config set server.bind ''`
writes `bind = ""`. The only difference is that a file's `""` *does* override a lower file (e.g. a user
`tls.cert`), where an empty variable would not. That is how a hand-written `""` behaves today (Non-goal:
merge unchanged; no unset). `logging.file ''` is refused under R2, which is consistent with it being an
unset in env. Worth one sentence in the docs (R9), not a code change.

## 5. Scopes; `smart_coerce`

- The kind depends on the path only; `update(self.site_name, …)` is unchanged, so `--user/--system/--local`
  behave alike. Probed `--user` with `HOME=<site>/home` (macOS user path is under `$HOME`).
  `--system` cannot be tested unprivileged.
- `smart_coerce` stays in `core/types.py`: it is still used by `task.py:1084,1186,1190,1293`,
  `core/template.py:213` and `core/tag.py:39`. Only the import at `config.py:30` goes.

## 6. Red tests — `tests/test_config_set_types.py`

Every test drives `hs config set PATH VALUE --local` as a subprocess and reads the TOML back with
`tomllib`. No new API is imported, so on `d9901cf0` each red test fails in its call by an assertion.
**Harness facts:**
- conftest sets `HYPERSHELL_CONFIG_FILE=''`, which makes `--local` write to path `''` (FileNotFoundError,
  rc 5, `core/config.py:573-574`). The child env must set
  `HYPERSHELL_SITE=<site>` and `HYPERSHELL_CONFIG_FILE=<site>/config.toml`.
- In-process `ConfigSetApp.main` is unusable, because paths are fixed at pytest import.
- Compare values with `type(v) is type(e) and v == e`, since `1 == True`.
- For refusals, seed `config.toml`, then assert rc 2, then `f'{path}: expected {form}'` and
  `repr(value)` in stderr, no "traceback", the file byte-equal, the set of `.config.*.toml` unchanged
  and no `exception-*.log`. Don't diff the whole site: `Library/` and `Logs/` appear on import.
- Marker: the repo marks CLI-subprocess drives `@mark.integration` (`test_env_config.py`,
  `test_key_option.py`). This is recommended over `@mark.unit`; the whole file runs in about 25 s
  serially.

**Red on `d9901cf0` (38)** — all were verified as AssertionErrors:
- `test_text_kept[...]`: `auth-digits` (server.auth 0012345678901234), `launcher-false`, `export-007`,
  `datefmt-null`, `format-None`, `level-True`, `variant-env-007` (server.auth_env), `variant-eval-true`
  (database.password_eval), `nodelist-group` (ssh.nodelist.gpu TRUE), `zone-launcher` (false),
  `zone-launcher-args` (1e3)
- `test_typed_converted[...]`: `eager-no`, `eager-On`, `eager-1` (needs `is True`), `logging-file-enabled`
- `test_typed_refused[...]`: `port-abc`, `port-fraction`, `port-empty`, `factor-inf`, `factor-nan`,
  `eager-maybe`, `eager-empty`, `relay-min-none`, `zone-size-x`, `logging-file-false`, `logging-file-empty`
- `test_duration_size[...]`: `evict-inf` (→ `"inf"`), `evict-True`, `evict-null`
- `test_untyped[...]`: `pragma-007`, `connect-True`, `pragma-null`, `connect-hex` (→ 31), `pragma-dot5`,
  `closed-unknown-none` (server.nothing none)
- `test_auth_digits_read_back` (`hs config get -r server.auth` prints `0012345678901234`; R1 without
  starting a server)
- `test_scope[--user]`, `test_scope[--local]` (export.RUN_ID 007; `--user` under `HOME=<site>/home`,
  located via `hs config --user`)

**Guards, passing on `d9901cf0` (25):**
- `test_text_kept`: `export-empty`, `bind-empty`
- `test_typed_converted`: `eager-false`, `port`, `port-underscore`, `factor`, `ratelimit-exp`,
  `relay-min-none`, `relay-min`, `zone-size`, `logging-file-TRUE`, `logging-file-path`
- `test_duration_size`: `evict-600`, `evict-half`, `evict-10m`, `memory-16G`
- `test_untyped`: `pragma-wal`, `connect-int`, `connect-underscore`, `provider-true`, `section-case`
  (Server.eager yes → "yes"), `below-key`, `unknown-empty`, `zone-unknown-key`
- `test_one_word_path_refused` (`database x` → rc 2, no file)

Non-red additions for the build (they import the new API, so they stay out of `red.txt`): a
`match_path` table in `tests/test_config_schema.py` covering every row of §1. Also add a
`logging-file-empty` case to that file's `REFUSED` for `read_value`. It fails on base by pytest's
"DID NOT RAISE" and could be listed red if wanted. On the prototype, `test_config_schema.py`,
`test_main.py` and `test_env_config.py` stay green (264 passed).

Out of scope, noted: integers beyond 64 bits (`1234567890123456789012`) are written although TOML
forbids them; the env layer does the same.
