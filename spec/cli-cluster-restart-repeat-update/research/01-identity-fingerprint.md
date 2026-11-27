# Research 01 — Stable task IDENTITY fingerprint (GOAL R2)

> **NAMING UPDATE (2026-07-11, maintainer):** the column is named **`Task.fingerprint`** (not
> `identity` — too close to `id`), matching `Source.fingerprint`. Read "identity" below as the
> **`Task.fingerprint`** value; the classmethod is `Task.compute_fingerprint`. Formula/seam unchanged.
> See [`00-digest.md`](00-digest.md).

Resolves the formula and the exact code seam for a per-task IDENTITY: a canonical,
order-independent hash of **pre-template args + group + tags**, used by `--restart` /
`--update` gating to detect which tasks already landed. All claims grounded in source.

## Where template expansion happens vs. Task creation

Expansion happens in the **Loader FSM**, one step *before* `Task.new`:

- **Line tasks** — `Loader.load_line_task` (`submit.py:164-167`):
  ```
  args = self.template.expand(str(line).strip())          # 166  (expanded)
  self.task = Task.new(args=args, ..., tag=self.tags)     # 167
  ```
  The raw pre-expansion line is `str(line).strip()` — **available at line 166**, but it is
  *not* passed to `Task.new`. Note the raw line still contains any inline `# HYPERSHELL:`
  comment; the comment is stripped later by `Task.split_argline` (`model.py:363-379`), which
  currently runs *inside* `Task.new` on the **already-expanded** string.
- **JSON records** — `Loader.load_json_task` (`submit.py:190-196`):
  ```
  base = str(record.get('args', ''))                     # 190  (pre-expansion command)
  args = self.template.expand(base, context=record)      # 191  (expanded)
  ...
  Task.new(args=args, ..., tag=tags, strict_tag=False, parse_inline=False)  # 195
  ```
  Pre-template args for a JSON record = **`base`** (the raw `args` field). `parse_inline=False`,
  so no inline-comment splitting for JSON.

`Task.new` (`model.py:333-361`) stores the **already-expanded** string in `Task.args`
(`model.py:359`). So R2's "args before template expansion" is **not** recoverable from the
persisted row — the raw string must be threaded through the seam at submit time.

## How tags/group/resources are assembled (`Task.new`, `model.py:349-361`)

1. `split_argline(args)` → `(args, inline_tags)` when `parse_inline` (line 351); else verbatim.
2. Final tag dict = `{**(tag or {}), **inline_tags, **{'part': 0}}` (line 354) — global tags +
   inline tags + a **bookkeeping `part:0`** tag.
3. `group/cores/memory/timeout` are **popped out of the tag dict** into columns
   (`model.py:355-358`), so they do **not** remain in the stored `tag`.
4. `part` is later **mutated** by `rotatedb` (`data/__init__.py:139`, `json_set($.part, N)`).

Consequences for identity membership:
- **Exclude `part`** — it is injected bookkeeping and is rewritten on rotation; including it
  would make identity unstable across `hs initdb --rotate`.
- **Exclude `cores`/`memory`/`timeout`/`group` from the tags portion** — they are popped from
  the tag dict anyway; `group` is a *separate* identity input per R2. Resource knobs describe
  *how* a task runs, not *what* it is; keep them out.
- Net: if identity is computed from the **final** `tag` dict (post-pop) minus `part`, exclusion
  is automatic — the only keys left are true user tags (global + inline + JSON record keys).

## JSON-record stability

For JSON, `record_tags` (`submit.py:192-193`) are the record keys (≠`args`), nested values
JSON-serialized with `json.dumps(..., separators=(',',':'))`, merged over global tags
(`submit.py:194`). Identity over `base` + `group` + these tags is stable because the named
`{key}` context expansion (line 191) does **not** touch `base`. That is exactly the point:
identity keys off pre-expansion `base`, so re-running the same JSON file yields identical
identities regardless of template.

## RECOMMENDATION

**Formula.** For each task compute:
```python
payload = json.dumps(
    {'args': raw_command, 'group': group, 'tags': identity_tags},
    sort_keys=True, separators=(',', ':'), ensure_ascii=False,
)
identity = hashlib.md5(payload.encode()).hexdigest()   # text, 32 hex chars
```
where
- `raw_command` = **pre-expansion** command with inline comment removed:
  - line task: `Task.split_argline(raw_line)[0]` (run on the *raw* line);
  - JSON: `base` verbatim (no inline parsing).
- `group` = the resolved group int (`other['group']` after the pop).
- `identity_tags` = `{k: v for k, v in final_tag.items() if k != 'part'}` (post-pop dict).

`sort_keys=True` gives the order-independence R2 requires (both across tag insertion order and
JSON key order). MD5 is fine here (non-security fingerprint) and is **consistent with the SOURCE
content-md5** the GOAL already specifies. Store in a new nullable `Task.identity` TEXT column
(`UUID`-style `TEXT`; add to `columns` dict + consider an index for gating lookups).

**Where to compute — `Task.new`, not the Loader.** Invariant #1 ("task-state/identity logic
belongs in `Task` classmethods", `invariants.md:33`) says the canonicalization+hash should be a
`Task` classmethod (e.g. `Task.compute_identity(raw_command, group, tags)`), called from
`Task.new` right after the tag/group assembly (`model.py:358`) and before the return.

**Threading the raw line through.** Add an optional `raw_args: str = None` kwarg to `Task.new`.
- `load_line_task`: `Task.new(args=args, raw_args=str(line).strip(), ...)`.
- `load_json_task`: `Task.new(args=args, raw_args=base, ...)`.
- Inside `Task.new`: `raw_command = Task.split_argline(raw_args)[0] if parse_inline else
  str(raw_args).strip()`; fall back to the stored `args` when `raw_args is None` (direct/retry
  paths — harmless since those are gating-exempt).

**Retry chain.** `__schedule_next_failed_tasks` (`model.py:555`) calls `cls.new(args=task.args,
...)` with the *stored expanded* args and no raw line. Retries are the *same* logical task, so
**copy the parent's identity**: add `identity=` kwarg to `Task.new` and pass
`identity=task.identity` here so the whole `previous_id`/`next_id` chain shares one identity
(gating must count a retried task as "landed"). Do not recompute from expanded args there.

**Reserved sources.** `<direct>` (`submit_one`, `submit.py:1101`) and `<stdin>` are exempt from
gating (GOAL). Identity may still be populated uniformly (cheap) — exemption is enforced by the
SOURCE record (separate brief), not by nulling identity.

## Flag: tags-in-identity consequence (GOAL clarification)

Because tags feed identity, **re-tagging an otherwise-identical command changes its identity**.
Under `--update` the re-tagged task therefore reads as *novel* and is resubmitted; under
`--restart` it will not match a prior landing. This is the intended semantics per the GOAL
("`--update` submits only novel tasks after the file changes") but should be called out in
PLAN/docs: changing `-t`/global tags or inline `# HYPERSHELL:` tags == a new task identity.

## Open questions

- Hash choice: MD5 (matches SOURCE md5, non-crypto) vs SHA-256. Recommend MD5 for consistency
  unless the maintainer prefers SHA-256 collision margin.
- Should global `-t` tags be part of identity at all, or only inline/record tags? R2 says "tags"
  (all of them) — recommend all, but this is the sharpest UX edge (re-`-t` ⇒ novel).
- `cores/memory/timeout`: confirmed excluded (popped before identity). Verify the maintainer
  agrees these are not identity-bearing.
