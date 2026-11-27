# Research 03 — R3: a restarted upstream's stale proxy ids (`core/reconnect.py`)

Line numbers are `develop` at `2ffb5cc` (2.9.0a8).

## How it happens

- `UpstreamLink` keeps one client per connection (`self.client`, set in `attempt`,
  `reconnect.py:433`; `epoch` +1 at `:434`). `QueueClient.connect` (`core/queue.py:1087`) mints
  the `scheduled/completed/heartbeat/confirmed` proxies on the server it dials. `dispatcher` is
  minted lazily on first use (`queue.py:1115`).
- `call` (`reconnect.py:258`) creates a role's `_Worker` (a new thread) on that role's first call
  in an epoch (`:270-272`). A stock `BaseProxy` caches one connection per **thread and address**
  (`_tls.connection`, shared by every proxy to that address). A new thread's first
  `_callmethod` dials afresh (`_connect`: there is no incref on that path) and sends
  `(self._id, method, …)`.
- If the server restarted while the link stayed UP (no role's call has failed yet), that fresh
  dial reaches the **new** server. Its `serve_client` fails on `id_to_obj[ident]` with `KeyError`.
  That `KeyError` is raised outside the method call, so it goes back as `'#TRACEBACK'` and the
  client raises `RemoteError(<traceback text>)`. That is neither fatal (`:89`) nor transport
  (`:92`), so `call` re-raises it unchanged (`:291`).
- A role whose worker already exists dials nothing new. It reuses its cached connection, gets
  `EOFError`/`OSError`, and is already handled (`mark_down` → `LinkDown` → reconnect → new client,
  new epoch, new workers). Only a **role's first call on a new thread in an epoch the restart
  post-dates** is exposed, and no transport error need come first. That is why the window is narrow
  in a whole relay.
- The roles and what they do with each outcome:

| Role | Call site | On `LinkDown` (today) | On `RemoteError` (today) |
|---|---|---|---|
| puller | `relay/upstream.py:419` request, `:458` confirm | `WAIT_LINK`, then re-request or re-confirm | escapes the FSM → relay exit 5 |
| heartbeat | `upstream.py:827` beat, `:906` topology | `WAIT_LINK`, roster resent | beat: escapes; topology: warning only (`:910`) |
| returner | `upstream.py:653` | bundle kept, resent after reconnect | escapes |
| handback | `relay/__init__.py:811` | stays due, retried at each check (`:619`, `:688`, `:722`) | `refuse_hand_back` (`:814`, `:830`) → left to eviction |

- A retried hand-back on the restarted server does no harm. `Dispatcher.handback`
  (`core/dispatch.py:459`) only queues it. `Task.revert_handed_back` (`data/model.py:1050`, called
  at `server.py:406`) is conditional on id + `schedule_time` + holder + no completion, so rows the
  new server already requeued at start (`revert_interrupted`, `server.py:232`) are skipped
  ("already settled"). The relay then drops them, as upstream has them back. A stale-id failure is
  raised **before** the method runs, so a retried put, request or confirm is never a duplicate.

## Probes (`$TMPDIR/rh-03/`, not in the worktree)

`probe.py` sets up the seed's harness:
1. Start a `QueueServer(port=0)` and point an `UpstreamLink` at it (deadline 5 s, backoff
   0.01–0.02 s).
2. Mint the dispatcher with a warm-up `link.call(<other role>, lambda c: c.dispatcher)`.
3. Run `old.shutdown()` and keep `old` referenced. Start a new `QueueServer` on the same port.
4. Make the first call of each role with its real call shape: `dispatcher.request(request_text(…))`,
   `heartbeat.put(Heartbeat…pack())`, `completed.put(b'[]')`, `dispatcher.handback(handback_text(…))`.

- **develop, 3.15.2 and 3.12.13:** all four roles raise `RemoteError`. The text ends
  `…in serve_client\n    obj, exposed, gettypeid = id_to_obj[ident]\n…KeyError: '10a2cf820'\n`,
  and the key is the stale proxy's `_id` (it matched `client.completed._id`).
- **`--patch`** (monkeypatch `is_transport = orig or is_stale`, where `is_stale` = `RemoteError`
  whose `args[0]` ends `^KeyError: '[0-9a-f]+'\s*\Z`): on both Pythons, every role gets
  `LinkDown` first. After `wait_up` its same call succeeds on epoch 1→2: puller
  `{"tasks": [], "shutdown": false}`, heartbeat and returner `None`, handback a reply (an error
  reply because the server has no database, which is not a refusal on the link's account).
- `genuine.py`: a `Dispatcher` method that raises `KeyError('a1b2c3')` arrives as a builtin
  **`KeyError`**, not `RemoteError` (`'#ERROR'` path). A HyperShell method's own `KeyError` can
  therefore never look like a stale id.
- `serve_client`'s id lookup, `raise ke` and `'#TRACEBACK', format_exc()` are identical in
  3.11.18, 3.12, 3.13, 3.14 and 3.15 (source read; 3.11, 3.13 and 3.14 were not run).

## Options

- **(A) Classify a stale-id `RemoteError` as a transport loss.** A predicate (`is_stale_proxy`)
  matches `RemoteError` whose `args[0]`'s last line is `KeyError: '<hex>'`. Optionally also
  require `serve_client` in the text. `call` then takes the existing `mark_down` → `LinkDown`
  path. Every role already handles `LinkDown` as "after any outage" (table above). It needs ~10
  lines in `reconnect.py` only: no `core/queue.py`, no wire change. A false positive is one
  needless reconnect, bounded by the backoff. A false negative is today's behaviour.
- **(B) Rebuild proxies per thread or generation.** Detection is the problem. The link cannot know
  the server changed until a call fails, and a new thread's fresh dial succeeds. A generation
  counter only moves on a detected loss, which is already handled. Pre-dialling every role's worker
  at connect time narrows the window but does not close it (a restart between connect and the
  warm-up). It also needs the link to know its roles up front and adds calls per connect, and it
  still needs (A) for the residue.
- **(C) Both:** B buys nothing that A does not already cover.

## Recommendation

**(A), in `reconnect.py` only.** Add `is_stale_proxy(error)`: `isinstance(error, RemoteError)`
and the last non-blank line of `str(error.args[0])` matches `KeyError: '[0-9a-f]+'`. OR it into
`is_transport` (`:101`), which keeps `attempt` and `call` consistent and harmless at dial.
`call` should then log a short reason, for example `upstream server restarted (unknown proxy id
…)`, not the whole remote traceback. As it stands `mark_down`'s reason and the `LinkDown` text
embed the full traceback (seen in the probe). Update the module docstring's error classes.

It is the only option the probe shows meeting R3 for all four roles: it never raises past
`call`, and every role reconnects and carries on. It does not touch high-blast-radius files.

**R10:** `core/reconnect.py` is imported only by `relay/__init__.py:58` and `relay/upstream.py:59`
(plus tests). A plain `hs client` uses `QueueClient` directly (`client.py` does not import it), so
nothing changes for plain clients, servers or peers of 2.9.0a8.

## R9 test shape

Add to `tests/test_relay_link.py` (`@mark.integration`), with no sleeps:

- **Restart test:** `@mark.parametrize('role', ['puller', 'heartbeat', 'returner', 'handback'])`,
  using the role's real call shape as in `probe.py`.
  - Set up `QueueServer(QueueConfig(host='127.0.0.1', port=0, auth=…, size=2))` and an
    `UpstreamLink(deadline=5, backoff_base=0.01, backoff_cap=0.02)`; wait with `wait_up(DEADLINE)`.
  - Warm up through another role with `c.dispatcher`. Shut the old server down, **keep it
    referenced** (in-process `id()` reuse could otherwise let the new server mint the same id), and
    start a new server on `old.address[1]` (the port rebound at once in every probe run, about 20 in all).
  - Assert the first call raises `LinkDown` (or returns) and never `RemoteError`. Then
    `wait_up(DEADLINE)`, the same call succeeds, and `link.epoch` has advanced.
  - Stop the link and both servers in `finally`. On `2ffb5cc` it fails with `RemoteError`.
- **Predicate unit test:** a real-format stale text gives true. False for `RemoteError` of other
  text (a "not in exposed" `AttributeError` traceback), a bare `KeyError`, and
  `RemoteError('Unserializable message: …')`.
- **Optional, role level:** `RelayThread.hand_back` through the restarted link leaves `handback_due`
  true and logs no "Could not hand back", and `UpstreamPuller.request` returns `WAIT_LINK`. That
  needs `bare_relay`/`held`-style fixtures from `tests/test_relay_retire.py`.

## Risks / for the planner to decide

- **Text match vs. id match.** Matching the key against the current client's proxy `_id`s is
  stricter, but it reads a `BaseProxy` internal not covered by `unsupported_internals`
  (`queue.py:451-475`). If chosen, add a check there, which touches `core/queue.py`. I recommend
  the text match.
- **Coordination with R4.** That change also alters the classifier: `ssl.SSLError` at the first
  connection becomes fatal. Keep R3's change in `is_transport` or `call`, and have R4's in
  `attempt`, so neither undoes the other.
- **Flapping.** A stale id cannot recur on a new connection, since its proxies are freshly minted,
  so no reconnect loop is expected. A restarted server that by chance mints the *same* id string
  for a different object (an address collision across processes, unlikely under ASLR) would not be
  caught. It was not observed and is out of scope.
- **Handback detail.** Should a `RemoteError` other than a stale id still `refuse_hand_back`? I
  recommend yes: that is today's contract for an older server.
