# 04: the `#` parser and integer group parsing

## Question

What changes when `Task.split_argline` stops cutting at a bare `#` (R1, R2)? Who calls it, how does
it interact with `--template`, and which task identities drift (C4)? Separately, how do group values
get typed today, and where should one integer-group check live (R3, R4, C5)?

## Findings

**Callers of the `#` rule.** Every caller goes through `Task.split_argline` (`data/model.py:592-607`).
Nothing re-parses downstream.

- `Loader.load_line_task` (`submit.py:354-377`) expands the template on the raw line, then calls
  `Task.new(parse_inline=True)`, which splits the *expanded* text (`model.py:532`). The fingerprint
  splits `raw_args` again (`model.py:555-559`). Tag-only lines re-split (`:369`) and write
  `self.tags[k]`.
- `line_is_task` (`submit.py:130-139`) does the same expand-then-split, so
  `source_fingerprint_and_count` (`:142-165`) counts what the Loader does. `hs submit` passes its
  template (`:1567`), and `hs cluster` passes `DEFAULT_TEMPLATE` (`cluster/__init__.py:559`).
- `hs cluster` and `hs server` with line input ingest raw lines with `DEFAULT_TEMPLATE`
  (`server.py:1194-1206`). The client expands the template on the stored, already-cut `args`
  (`client.py:783`) and does not re-parse. The no-database path (`LiveSubmitThread`) uses the same
  Loader.
- `SubmitApp.submit_one` (`submit.py:1467-1474`): argv is joined with `quote_arg` (`:1603`), which
  quotes only arguments that contain whitespace, and then goes to `Task.new(parse_inline=True)`.
  The legacy `task` group's submit and run commands (`task.py:114`, `:445`) join argv without any quoting.
- `--from-json` uses `parse_inline=False` (`submit.py:389-391`), so it is unaffected by R1. `hs
  update` never parses `#`. `relay/` has no parse. `core/sys.py:52` is unrelated.
- Tests that pin the cut: `tests/test_data/test_model.py:29-33` (`echo "hello world" # Comment` →
  `echo "hello world"`). These flip under R1.

**The template interplay (probed, `hs submit --template 'echo {}'`).** A file holding `a`, `# comment`,
`# HYPERSHELL: group:2`, `b` and a blank line gives **5 tasks**: `echo a`, `echo`, `echo` (group 2),
`echo b`, `echo`. So under `--template`, `hs submit` today makes tasks out of the comment line, the
blank line and the tag-only line (which sets nothing globally). After R1 alone, the comment line
would become `echo # comment`, still a task. `--template '{} > out'` with a marker line exits 5,
because the template tail is parsed as tags. `hs cluster` does not have this problem: it splits the
raw line on the server, so comments and blank lines are skipped. The probe ran 3 tasks for the file
`a`, `# comment`, `b #c`, blank.

**Pre-existing crash: a tag-only line under `hs cluster` or `hs server`.** `hs cluster t.in -N1`, with
or without `--no-db`, on a file holding `echo a`, `# HYPERSHELL: group:2`, `echo b` exits **5**.
`Loader` raises `TypeError: 'NoneType' object does not support item assignment` at `submit.py:374`,
because the server's `SubmitThread` passes `tags=None`. R2 ("tag-only line sets tags exactly as
today") and the R8 tag-only tests through `hs cluster` cannot pass until this is fixed. The fix is
`self.tags = dict(tags or {})` in `Loader.__init__`, which also stops the Loader mutating
`SubmitApp.tags`, a class-level `{}` (`:1401`).

**Argument input.** `hs submit -- '#x'` stores a task with `args ''` (exit 0). `hs submit -- echo '#'
foo` stores `echo`, and after R1 `echo # foo`: `quote_arg` drops the user's quoting of `#`, so the
shell then treats it as a comment. Both are pre-existing. The plan may refuse an empty command
from argv, and may have `quote_arg` quote an argument that starts with `#`.

**Shell semantics (`/bin/sh`, confirmed).** `echo a #b` prints `a`. `echo a#b` prints `a#b`. `echo
"x" # trailing` prints `x`. `$#` prints `2`. `echo '#'' HYPERSHELL:'` prints `# HYPERSHELL:`. So a kept
trailing ` # note` is harmless (C3). The escape works after R1, because `#\s*HYPERSHELL` does not match
`#'' HYPERSHELL`; today the line is cut to `echo '`.

**Group typing today.** Tag text goes `Tag.from_cmdline` → `smart_coerce` (`core/types.py:36-50`):
null/none → `None`, true/false → `bool`, then `int(text)`, then `float(text)`, else `str`. Because
`int()` is tried first on the stripped text, **a tag value is an `int` (not `bool`) exactly when
`int()`, and so `-g` (`type=int`, `submit.py:1370`), accepts the text.** Probed:

| Text | Result |
| --- | --- |
| `07`, `+5`, `-3`, ` 7 `, `1_000`, `٣` | int 7, 5, -3, 7, 1000, 3 |
| `1e3`, `1.5`, `nan`, `inf` | float |
| `0x10`, `x`, `''` | str |
| `true`, `False` | bool |
| `null`, `None` | None |

JSON values arrive typed, so the same predicate also implements C5: `isinstance(v, int) and not
isinstance(v, bool)`.

**Huge integers.** `-g 99999999999999999999` exits 5 with a traceback (SQLite `OverflowError`). `-g
3000000000` is stored on SQLite but is outside PostgreSQL `INTEGER` (`model.py:341`, int4). A file
line `group:99999999999999999999` kills `DatabaseCommitter`, and **`hs submit -f` hangs** (killed at
40 s, rc 124), the same failure mode as `group:nan`. Under `--no-db` it runs (exit 0).

**Other ways a group gets set.** Retries copy `task.group` (no new value). `hs update group=` is a
non-goal. `hs list -t` and `hs update` tag args use `Tag.parse_cmdline_list` (`task.py:515`,
`:1079`), so do not put the check in `Tag.from_cmdline`. The legacy `task` group's submit and run, given `-t group:x`, reach
`Task.new` and would be covered by a check there. Queue mode: `SubmitApp.check_tags` runs only in
the database branch (`submit.py:1444-1445`), so **`-t` is silently ignored with `--queue`** today, and
the group stays `None` there (`:1447-1451` resolves it only with a database).

## Recommendation

**`split_argline`.** Keep the regex. Decide whether a line is a comment before looking for the marker:

```python
MARKER: Final = re.compile(r'#\s*HYPERSHELL:?')   # unchanged: colon optional, case-sensitive

@classmethod
def split_argline(cls, args: str) -> Tuple[str, Dict[str, JSONData]]:
    args = str(args).strip()
    if args.startswith('#') and not MARKER.match(args):
        return '', {}                                  # full-line comment (R2)
    if match := MARKER.search(args):                   # tag-only line, or marker after a command
        tags = <parse args[match.end():] as today; InvalidGroup re-raised unwrapped>
        return args[:match.start()].strip(), tags
    return args, {}                                    # no cut (R1)
```

This handles each case as follows:

- `echo a#b # HYPERSHELL: group:1` → `echo a#b`, group 1 (today too).
- A marker inside quotes is still found, and `echo '# HYPERSHELL: group:3'` still exits 5 (the
  non-goal, confirmed).
- `#HYPERSHELLX: a:1` keeps today's no-colon match.
- Two edges change from today:
  - `## HYPERSHELL: x:1` is a task `#` with tag x today and becomes a comment.
  - `# c # HYPERSHELL: x:1` is a task `# c` today and becomes a comment.

  If the plan prefers, a `#`-first line can count as tag-only whenever `MARKER.search` hits.
  Name the choice in TECH.

**Split the raw line before the template expands.** In `Loader.load_line_task` and `line_is_task`:

1. `command, inline = Task.split_argline(raw)`.
2. If `command` is empty, it is a tag-only line or a skip.
3. Otherwise `args = template.expand(command)`, then
   `Task.new(args=args, raw_args=command, parse_inline=False, tag={**self.tags, **inline})`.
4. `line_is_task` returns `bool(command) and bool(template.expand(command).strip())`.

This gives `hs submit --template` what `hs cluster` already does, and makes R2 hold under
`--template`. The fingerprint is unchanged, because it was already taken over the split of the raw
line. Drift:

- Blank lines, comment lines and tag-only lines under `hs submit --template` stop being `echo`
  tasks.
- A `#` or marker inside the *template text* is no longer parsed.

Also fix `Loader.__init__` (`self.tags = dict(tags or {})`).

**Group helper** (`data/model.py`, beside `DEFAULT_TASK_GROUP`):

```python
GROUP_MIN, GROUP_MAX = -2**31, 2**31 - 1          # PostgreSQL INTEGER; same rule on SQLite (C8)

class InvalidGroup(RuntimeError):                  # RuntimeError -> exit 5 via the shared mapping
    def __init__(self, value: Any, origin: str | None = None) -> None: ...
    # message: f'Task group must be an integer: {value!r}' + (f' ({origin})' if origin else '')
    def at(self, origin: str) -> InvalidGroup: ...  # same value, origin added ('line 7', 'record 3')

def ensure_group(value: Any) -> int:               # typed value: tag (post-smart_coerce) or JSON
    if isinstance(value, bool) or not isinstance(value, int) or not GROUP_MIN <= value <= GROUP_MAX:
        raise InvalidGroup(value)
    return value

def parse_group(text: str) -> int:                 # argparse `-g` type=, so -g and tags share one rule
    try: return ensure_group(int(text))
    except ValueError: raise InvalidGroup(text)    # argparse needs ArgumentTypeError: wrap at the option
```

Where the check is applied:

- In `Task.new`, check only a group that came from a tag:
  `if 'group' in tag: other['group'] = ensure_group(tag.pop('group'))`. The caller's `group` may
  legitimately be `None` in queue mode.
- `split_argline` raises `InvalidGroup` for the inline tag and the tag-only line without wrapping
  it in its `RuntimeError` (exclude `InvalidGroup` from the `except`).
- `SubmitApp.check_tags` converts it to `ArgumentError`, so `-t group:x` exits 2 (R4). It must also
  run in queue mode (C8).
- JSON is checked in `validate_json_expansion`, or a sibling pre-pass, raising
  `InvalidGroup(...).at(f'record {i}')`, never `ArgumentError`, which exits 2 rather than 5. That
  function numbers records from 0 today; pick 0- or 1-based once.

**Line numbers.** `Task.new` cannot know them. The pre-scan that already reads every line,
`source_fingerprint_and_count`'s text pass, uses `enumerate(stream, 1)`, catches `InvalidGroup` and
re-raises `.at(f'line {n}')` before any insert (R4). `hs cluster --no-db FILE` needs that same
pre-scan. For streams (R5), the Loader keeps a `self.lineno` over every line read, comments and
blanks included, then catches, logs an ERROR naming value and line, and continues without changing
`self.tags`.

The error text loses the original spelling (`1e3` shows as `1000.0`). To name the text exactly,
take it from the token (`tok.partition(':')[2].strip()`) in `split_argline` and `check_tags`.

## Identity impact

Probed with the proposed split monkeypatched into `hsx`:

- **Changed:** `echo 'issue #42'`, `echo a#b`, `awk … # trailing`, and `echo '#'' HYPERSHELL:'`
  (which today is cut to `echo '`).
- **Same:** plain lines, marker lines (`echo a # HYPERSHELL: group:1 x:2`), `echo a#b # HYPERSHELL:
  group:1`, full-line comments, and tag-only lines.
- `## HYPERSHELL:` and `# c # HYPERSHELL:` stop being tasks.

A 5-line file was first run by today's code: 4 tasks; `echo 'issue` failed with exit 2. Then
`hsx t.in --restart` with the new code: the md5 matches, so the matched source is reused. It logs
"4 present … 2 tasks already present; submitting 2 new tasks", the two drifted lines run as written
under the same source id, and no incomplete warning appears. `_warn_if_incomplete` warns only when
the tasks landed are fewer than `task_count`; here 6 ≥ 4. A second restart submits 0. The upfront
count is the same for normal files, because cut lines stayed non-empty. It changes only for the
`##`/`# c #` edges and, under `hs submit --template`, for comment, blank and tag-only lines.

## Risks/unknowns

- **The stale cut row stays.** In the probe, the old failed `echo 'issue` row kept "Failed task
  group 0" on every restart, so group 1's task never ran, although the corrected line succeeded.
  The release-note audit text should say that a cut row which failed still blocks later groups
  until it is cancelled or updated.
- **R7 collides with C4.** Drifted lines are *not* skipped by the gate, so they are "being
  submitted". On a stranded or advanced database, a `--restart` of an old file whose drifted line
  sits below the reached group would be refused with exit 2. The plan must decide this, for example
  by exempting a reused (matched) source.
- `quote_arg` and the legacy `task` group's submit join cannot preserve a quoted lone `#`.
- No PostgreSQL probe was run in this pass. The int32 bound is inferred from the column type.

## Probe evidence (abbreviated)

- `split_argline` today: `echo 'issue #42'`→`echo 'issue`; `## HYPERSHELL: x:1`→`('#',{x:1})`;
  `# c # HYPERSHELL: x:1`→`('# c',{x:1})`; `echo '#'' HYPERSHELL: x'`→`echo '`; quoted marker →
  `RuntimeError … "group:3'"`.
- `hs submit --template 'echo {}' -f t.in`: "Found 5 tasks"; `hs list args`: `echo a`, `echo`,
  `echo`, `echo b`, `echo`.
- `--template '{} > out'` with a marker line: `RuntimeError … Tag key ">:"`, exit 5.
- `hs cluster t.in -N1 [--no-db]` with a tag-only line: `TypeError … item assignment`, exit 5 (control
  file exits 0).
- `-g 99999999999999999999`: exit 5. `-g 3000000000`, `-g ' 7 '`, `-g 1_000`: stored 3000000000, 7, 1000.
- A file `group:99999999999999999999`: `OverflowError` in `DatabaseCommitter`, `hs submit` rc 124 at 40 s.
