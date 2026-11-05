# Research 03 — Client request path (Z2 planning)

Scope: the client side of the request that replaces `scheduled.get()`. Line numbers are against
this branch's head (`b54d685`) unless a ref is given.

## 1. Today's receive path

- `ClientScheduler` FSM (`client.py:143-267`): START → GET_REMOTE → UNPACK → PUT_CONFIRM →
  POP_TASK ⇄ PUT_LOCAL → GET_REMOTE. `get_remote` (`:206-222`) polls `check_signal()` (USR1/USR2 in
  standalone mode → FINAL), blocks `scheduled.get(timeout=DEFAULT_REMOTE_TIMEOUT=2)`
  (`core/queue.py:707`). On empty it re-enters, unless `-T` elapsed since `previous_received` → FINAL.
- End-of-work is a sentinel bundle: `deserialize_tasks` returns None → FINAL (`:224-231`). The
  server's `HeartMonitor.signal_clients` puts one sentinel per *registered* client on the shared
  queue (`server.py:640-646`). The scheduler never feeds executors their sentinels.
  `ClientThread.run_with_exceptions` does that after the scheduler joins (`:1237-1248`, `:1280-1286`).
  The order is scheduler → executors (N × `None` on inbound) → collector (`None` on outbound) →
  heartbeat FINISHED.
- `put_confirm` (`:238-245`) sends `ClientInfo(INSTANCE, HOSTNAME, task_ids)` (`:101-140`), which is
  skipped under `--no-confirm`.
- The client already prefetches. It is "ready" again as soon as the last task of the bundle is
  accepted by `inbound`. `inbound = Queue(maxsize=bundlesize)` (`:1220`), so it holds at most the
  current bundle, plus `-b` queued tasks, plus N running ones.
- **`-b` today is the collector's return size** (help `:1461`, "Bundle size for finished tasks"),
  and it also sizes `inbound`/`outbound`. Its CLI default comes from **`config.submit.bundlesize`**
  (`:1508`, likewise `bundlewait` at `:1511`), but the module default is `default.client.bundlesize`
  (`:293`). The docs document `[client] .bundlesize` (`docs/_include/config_param_ref.rst:298`).
  `AGENTS.md` § Configuration records the split.
- R2 makes `-b` also the advertised size. Giving it both meanings has no functional conflict. The
  collector size and the request size can stay the same number. Two things must change: the help
  text, and the snippets under R13.

## 2. Request FSM

Keep the shape and rename **GET_REMOTE → REQUEST**. Readiness keeps today's semantics: REQUEST is
entered from START and from POP_TASK when the list is empty. Waiting for "inbound empty" or
"executors idle" would add a round trip per bundle and put R1 at risk.

REQUEST behaves as follows:
1. Poll `check_signal()` as today.
2. Call `queue.dispatch.request({'client_id': INSTANCE, 'host': HOSTNAME, 'zone': zone|None,
   'size': advertised|None, 'version': __version__})`. The values are JSON-only (R3).
3. If the reply has `done` → FINAL. Executor sentinels then follow from `ClientThread`, unchanged.
4. If the reply has tasks → set `previous_received`, then UNPACK. `Task.from_json` is applied to
   `reply['tasks']`: a list of `to_json()` dicts, not the bytes from `serialize_tasks`, because
   R3 allows no bytes.
5. If the reply is empty → apply the `-T` check, then re-enter.

The finite-time bound lives in the server callable (a long-poll ≤ ~2 s), because a manager proxy
call has no client-side timeout. A proxy holds one connection per thread, so the call blocks only
the scheduler, not the heartbeat or collector. Add a short client backoff (≤
`DEFAULT_LOCAL_TIMEOUT`) only when an empty reply returns faster than ~0.1 s. That guards
against a server that answers instantly. It can live in a BACKOFF state that returns to REQUEST,
so that `halt()` stays observable.

UNPACK/PUT_CONFIRM/POP_TASK/PUT_LOCAL/FINAL stay unchanged. PUT_CONFIRM becomes redundant if the
server stamps `client_id`/`host` at cut time, since it holds them from the request. Keep it unless
the server plan drops `confirmed`. A lost connection still raises `EOFError`/`ConnectionResetError`
out of the scheduler, which ends in `handle_disconnect` with exit 5. A server callable must reply
`{'error': …}` rather than raise, because a raised exception reaches the client as itself.

## 3. Advertise-or-not (R2)

`Configuration.which(*keys)` names the winning layer (`default`, `preload`, `system`, `user`,
`local`, `env`). `blame()` wraps it (`core/config.py:247`). A prototype in scratch confirmed that it
reports `env`/`local` even when the value equals the default:
`HYPERSHELL_SUBMIT_BUNDLESIZE=1` → `env`, and a file with `bundlesize = 1` → `local`.

Mechanism:
- Set `-b` to `default=None` in `ClientApp`.
- `advertised = self.bundlesize if not None else (config.client.bundlesize if
  config.which('client','bundlesize') != 'default' else None)`.
- The collector and inbound size is `self.bundlesize or config.client.bundlesize`.

**Key choice: switch the client's `-b`/`-w` source to `client.*`.** That is the documented key.
Keeping `submit.bundlesize` would make anyone who tuned `hs submit` buffering in a config file
silently advertise that value from every plain `hs client`. The cost is a collector-size change for
users who relied on the undocumented `submit.*` coupling. The judge should confirm this.

API: `ClientThread`/`run_client(bundlesize: Optional[int] = None, zone: Optional[str] = None)`.
None advertises nothing and falls back to `DEFAULT_BUNDLESIZE` locally. An explicit int is
advertised. `LocalCluster` passes the server's own `-b` (`cluster/local.py:236`), and the remote,
ssh and autoscale launchers always pass `-b` (`cluster/remote.py:305,834`, `cluster/ssh.py:298`).
They all advertise the server's own value, which gives the same sizes as today.

## 4. `--zone` on `hs client`

`--zone NAME` has `default=config.client.zone`, and the new default key `client.zone` is unset.
The env name is `HYPERSHELL_CLIENT_ZONE`; plain `HYPERSHELL_ZONE` would expand to a top-level
`zone` key, which is Z3's to own. Validation in `check_args` (`:1596`): reject an empty or
whitespace-only name and control characters (`ArgumentError`, exit 2). Share one validator with
submit, task submit and cluster.

The client sends the **name** on every request and the server does the get-or-create lookup (R8).
A user who types the reserved default zone's name gets the reserved id. `cluster` passes `--zone`
to the clients it launches. A remote host's own `client.zone` config can still zone an unzoned
cluster's clients, which is a minor risk to document.

## 5. Version mismatch (R5)

**New client, old server.** I prototyped this against a stock manager. Creating an unregistered
typeid fails at once with `RemoteError` wrapping `KeyError: '<typeid>'`. Plan:
- Register the dispatch typeid only on the client role.
- Create it in a `QueueClient.connect_dispatch()` that `ClientThread` calls right after `with
  self.client:` and before `start_threads()`.
- Map that `RemoteError` to a new `ServerVersionError(RuntimeError)` with a message like "server
  does not support work requests; it is older than this client (HyperShell X)".

The existing `RuntimeError` mapping yields one critical line and `exit_status.runtime_error` (5)
(`core/exceptions.py:87,133`). Leave it out of the generic `QueueClient.connect` (`queue.py:824`)
so that `hs submit` against an old server is unaffected.

**Old client, new server.** A 2.9.0a2 client creates all four proxies inside `connect`
(`git show d996579:src/hypershell/core/queue.py`, L427-438), before any thread starts:
- If the server refuses or cuts that request, the client fails immediately: on `RemoteError` it
  writes a traceback file, and on `EOFError` it logs "server disconnected". Both exit 5, so it
  exits in bounded time.
- If the server instead keeps serving an empty `scheduled`, a client run without `-T` waits
  forever, which R5 forbids.

**Caveat for the server plan:** detection keyed on `create('_get_scheduled')` collides with the
in-memory server, whose `LiveSubmitThread` puts onto `scheduled` through a `QueueClient`
(`submit.py:744`, `server.py:823-827`).

## 6. `-N` vs `-b`

Advertise exactly `-b`, with no `max(size, N)`. When `-b` < N, short tasks can starve executors,
because each round trip feeds only `-b` tasks. That is the same property as today at server `-b 1`.
What is new: a client that explicitly says `-b 1` against `hs server -b 100` now gets 1 task per
reply. Two benchmark harnesses always pass a client `-b`
(`tests/cluster/drive.sh:213`, `tests/bench/bench_queue.py:382`), so R1 runs must account for it.

## 7. Test seams

Follow `tests/test_server_fsm.py`: a `SimpleNamespace(dispatch=FakeDispatch(replies), confirmed=Queue())`
drives `ClientScheduler` actions step by step. Monkeypatch `hypershell.client.DEFAULT_REMOTE_TIMEOUT`
and `hypershell.client.check_signal`. Cover:
- the payload is JSON-serialisable;
- `size` is None or an int;
- empty → REQUEST, and `-T` → FINAL;
- partial replies;
- `done` → FINAL;
- an error reply;
- the signal and `halt()` paths.

The version test is a stock `BaseManager` that registers only the four queues; check that
`ServerVersionError` arrives in bounded time and that `ClientApp` exits 5. `tests/test_client.py`
(42 lines) covers only executor slots. `tests/test_queue_workload.py:78-98` (Popen `hs client`) is
the template for R14's mixed-zone loopback run.

## Recommendation

- **FSM:** rename GET_REMOTE → REQUEST (long-poll call, `done`/empty/tasks), optional BACKOFF,
  everything else unchanged. Readiness = previous bundle handed to `inbound` (today's prefetch).
  Shutdown ordering is untouched.
- **Advertise:** `-b default=None`, plus `which('client','bundlesize') != 'default'`. Move the client
  `-b`/`-w` defaults to `client.*`. The API takes `bundlesize=None`.
- **Zone:** `--zone` / `client.zone`. Reject empty names and control characters. Send the name; the
  server resolves it.
- **Version:** a client-only dispatch probe at connect → `ServerVersionError` → exit 5, one line.
- **Risks:**
  - harnesses that pass an explicit client `-b` change R1 sizing;
  - the `submit.*` → `client.*` source change;
  - EOF exits 5 if the server stops before every requester has had `done` (the server must keep
    serving until heartbeats go FINISHED, as `HeartMonitor` does today);
  - old-client detection versus the in-memory submit path.
