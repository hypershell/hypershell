# REVIEW — TLS client reaches the server on the address family it listens on

> Adversarial QA by `hs-review`, run in an isolated/clean context. The correctness pass grades the
> branch diff against [`GOAL.md`](GOAL.md) + the AGENTS.md invariants **only** — it does not see
> `PLAN.md`/`TECH.md` (avoids grading-its-own-homework / plan-sycophancy). Every finding cites an
> **executed** command, not an assertion.

- **Reviewed commit:** `0afe639`  ·  **Base:** `develop` (`origin/develop` `b31c66e`)  ·  **Date:** 2026-11-03
- **Verdict:** approved
- **Cycle:** 1 of ≤3 — mirrors `review.cycle` in `TECH.md` (escalate to human on non-convergence)

**How this pass ran.** The correctness pass ran in a fresh reviewer context launched by the foreman.
That context read only `GOAL.md`, the spec-excluded diff (`git diff origin/develop...HEAD -- .
':(exclude)spec/'`), `git log --oneline`, `AGENTS.md`, `invariants.md`, `review-rubric.md` and the
repository. It did not read `PLAN.md`, `TECH.md` content, `research/`, `META.md`, or builder commit
bodies. Two orchestrator-side reads of `TECH.md` were limited and are recorded here. The first read
only the frontmatter keys, for `slug`, `kind`, `base` and `status`. The second was a single grep,
after the blind pass, for the R6 method statement. After the pass, the orchestrator also read only
the `## F` headings of `META.md`, to number its meta-notes. The `debate` and `completeness` variants
were not requested. `ReportFindings` is unavailable in this harness, so this file is the record.

**Contract drift (Step 1).** `GOAL.md` changed once after shaping: `7c9969a` resolves the open
clarification and adds **R7** (`free_port()` hardening). It is a legitimate clarification
resolution, recorded with its decider and date in the Clarifications section, and the foreman's
brief for this review includes the `free_port()` change. This review grades R1–R7.

## Verification run

All commands ran against frozen offline environments (`uv sync --frozen --offline`) built from
exported trees under `/tmp/claude-501/tlsaf-review/`, never against the worktree itself. There were
four trees: **head** (`0afe639`); **prefix** (head with `core/queue.py` restored from
`origin/develop`, so the new tests run against the pre-fix client); **master-trial** (the three 2.8.4
files applied with `git apply --cached` to a temporary index of `origin/master` `4d5771b`); and
**master-prefix** (`origin/master` plus only the two test files). The `master` environments omit the
`docs` group, which is not in the offline cache. CLI drives mirror `temp_site.sh` isolation in
Python: a fresh `HYPERSHELL_SITE` and `HYPERSHELL_DATABASE_FILE`, a blank `HYPERSHELL_CONFIG_FILE`,
scrubbed `HYPERSHELL_*` variables and a site-local working directory. The script itself was not
called because its exit trap runs `rm -rf`, which this session forbids. Every run had its own
process group and a kill timer.

- **Full suite, xdist `-n 6`:** 639 passed, 64 skipped on 3.11.18, 3.13.18 and 3.14.10. The only
  skips are the PostgreSQL, TimescaleDB and fixture-regeneration gates. No
  `test_queue_address_family` test skipped.
- **Full suite, serial, 3.12.16:** 639 passed, 64 skipped, with the same three gates (see the
  addendum).
- **`tests/test_queue_address_family.py` + `tests/test_queue_tls.py`, 3.12:**
  - head: 47 passed.
  - prefix: **3 failed**, 44 passed. The failures are `test_tls_client_skips_ipv6_listener`
    (`BrokenPipeError`), `test_cluster_skips_ipv6_listener[tls]` (`hs cluster` exit 5) and
    `test_no_ipv4_address_fails_promptly`.
  - master-trial: 47 passed.
  - master-prefix: the same 3 failed.
  - The cleartext twins and `[no-tls]` pass on every tree.
- **Resolver without `::1` for `localhost`** (a `-p` plugin that strips AF_INET6 results before the
  test's shim captures the resolver): the TLS trap test is still red on prefix and green on head.
  The shim's injection branch works on hosts whose resolver never offers `::1`.
- **Simulated hosts without IPv6** (`-p` plugin, three modes: `socket(AF_INET6)` raises EAFNOSUPPORT;
  binding `::1` raises EADDRNOTAVAIL; `socket.has_ipv6 = False`): each mode gave 100 passed and
  5 skipped over the new file, `test_queue_tls.py` and `test_cluster.py`. The skips are the four
  trap tests and the `free_port` unit test, each with a "no IPv6" reason. `free_port()` still works
  at import and for every test.
- **Flake stress:** 10 runs of the new file, `test_queue_tls.py` and `test_cluster.py` under
  `-n 4` on 3.14 gave 105 passed each time. A reverse-order run on 3.11 (`test_queue_tls.py`, the
  new file, then `test_client.py`) gave 50 passed.
- **R1 live, no resolver shim.** On this host `getaddrinfo('localhost')` returns `::1` first. The
  probe held a `::1`-only listener that accepts and resets, on the `hs cluster` port, and ran
  10 tasks:
  - head with TLS: exit 0 in 4/4 runs, 10/10 tasks recorded with `exit_status=0` and
    `completion_time` set, and 0 connections reached the IPv6 listener.
  - head and prefix with `--no-tls`: the same in 2/2 runs each.
  - prefix with TLS: exit 5 in 4/4 runs (`CRITICAL … BrokenPipeError`), 0 tasks done, and 1 hit on
    the IPv6 listener each run.
  - A 200-task run with `-N4` and TLS on head, against the same trap, exited 0 in 2/2 runs with
    200/200 tasks done and 0 IPv6 hits.
- **R4 drive, `hs client -H <host> -p <port>` with nothing listening, TLS and `--no-tls`, head and
  prefix:**
  - `localhost` and `127.0.0.1`: exit 5,
    `CRITICAL [hypershell.client] ConnectionRefusedError: [Errno 61] Connection refused`, in under
    1 s. This is identical on both trees and both transports.
  - `::1`: head gives exit 5, `HostAddressInfo: Could not resolve host '::1'`, in 0.4 s on both
    transports. Before the fix, TLS gave `ConnectionRefusedError` while cleartext already gave
    `HostAddressInfo`.
  - `no-such-host.invalid`: `HostAddressInfo`, exit 5, on both trees and both transports.
- **R5 probe** (a client context with `check_hostname=True` and a cert naming only `localhost`):
  dialling `localhost` succeeds with `server_hostname='localhost'`. Dialling `127.0.0.1` fails with
  `SSLCertVerificationError` (IP address mismatch). A mismatched `fingerprint` pin fails with
  `TLSError: Peer certificate fingerprint mismatch`.
- **R7 probe:** `held_on_ipv6_loopback(P)` returns True for each kind of listener that `::1` can
  reach: a V6ONLY `::1` listener, a V6ONLY `[::]` wildcard, a dual-stack `[::]` wildcard, and `::1`
  with SO_REUSEADDR or SO_REUSEPORT. It returns False for a free port.
- **R6 trial:**
  - `git merge-tree --write-tree --merge-base=20b1abd^ origin/master 20b1abd` auto-merges
    `core/queue.py`. Its only conflict is `spec/…/TECH.md`, which does not exist on `master` and is
    not shipped.
  - `git apply --cached --check` of the three-file patch onto an `origin/master` index applies
    cleanly.
  - `tests/conftest.py` and `core/tls.py` are byte-identical on `origin/master` and
    `origin/develop`.
  - `TECH.md` states the method: "Checked by trial apply, not by diff inspection" (orchestrator
    grep, `TECH.md:236`).
- **Seed claims (graded as prose):**
  - `SecureListener` (`queue.py:139-154`) refuses any family other than AF_INET and opens an AF_INET
    socket.
  - `find_available_ports` (`config.py:340-351`) probes with AF_INET.
  - `hs server --bind ::1` fails on both transports with `socket.gaierror` in the listener child.
    Observed on head; the code path is untouched by the diff.
  - `ROADMAP.md`'s `**Seed:**` link and `Refs:` path both exist.
- **Single dial site:** a grep of `src/` for `create_connection`, `getaddrinfo` and
  `socket.socket(` finds no other client dial. The stdlib `SocketClient` (3.11, 3.12 and 3.14 source
  printed) is `socket(AF_INET)` → `setblocking(True)` → `connect(address)`, which is the same
  sequence as the new `secure_client` dial.

*Addendum, serial suite (3.12.16):* 639 passed, 64 skipped in 335 s. The skips are the same three
gates, and nothing failed or hung.

## Requirement → evidence matrix

| R-ID | Implemented by (file/commit) | Verified how | Status |
|------|------------------------------|--------------|--------|
| R1 | `src/hypershell/core/queue.py:216-222` (`20b1abd`) | `test_cluster_skips_ipv6_listener[tls]` red on prefix and master-prefix, green on head and master-trial. The shim-free live drive gave head exit 0 with 10/10 tasks and 0 IPv6 hits, and prefix exit 5 in 4/4 runs. `--no-tls` gave exit 0 on both trees. | ✅ |
| R2 | `queue.py:219-222`: an AF_INET socket connected exactly as `multiprocessing.connection.SocketClient` does | For `localhost`, the trap tests (in-process and `hs cluster`) and the live drive show 0 connections to the `::1` listener. For remote names the guarantee is structural: an AF_INET socket cannot reach an IPv6 endpoint, and the call sequence matches the stdlib source. Not driven against a real multi-family remote name, because sandbox DNS and egress are blocked. | ✅ |
| R3 | `tests/test_queue_address_family.py:280-364` | Red before the fix and green after, on develop and on master. The no-`::1` resolver variant is still red before the fix. Three simulated no-IPv6 modes skip and do not fail. Cleartext twins pass on all trees. | ✅ |
| R4 | `queue.py:222` (resolution failure surfaces as `socket.gaierror`, mapped by `client.py:1593`) | The `hs client` drive matrix: no-server cases are unchanged (exit 5, `ConnectionRefusedError`). `::1` now matches cleartext (exit 5, `HostAddressInfo`, 0.4 s). `test_refused_when_nothing_listening` and `test_no_ipv4_address_fails_promptly` (red before the fix). | ✅ |
| R5 | `queue.py:223-238` unchanged (servername is `address[0]`, then `verify_peer_fingerprint`, then the auth challenge) | `test_queue_tls.py` is untouched by the diff and passes on all four Pythons. `test_hostname_check_uses_given_name`. The R5 probe shows both the negative host-name check and the fingerprint mismatch still enforced. | ✅ |
| R6 | Only the standard library (`socket`); the tests use helpers present in 2.8.3 | Trial apply onto `origin/master` is clean. The R3 file plus `test_queue_tls.py` pass there (47 passed) and go red without the client change. `TECH.md:236` names the method. | ✅ |
| R7 | `tests/conftest.py:17-54` (`draw_port`, `held_on_ipv6_loopback`, a bounded `free_port`) | The R7 probe over five listener kinds. `test_free_port_skips_port_held_on_ipv6_loopback`. The full suite passes on four Pythons under xdist and serially. No-IPv6 hosts are simulated without error. The draw loop is bounded at 32 and raises `RuntimeError`. | ✅ |

Unmapped changes (possible scope creep): none. `issues/ipv6-server-listener.md` and its `ROADMAP.md`
entry are the deferral named in GOAL's non-goals. They stay on develop and are not part of the 2.8.4
set.

## Findings

None. No CONFIRMED and no PLAUSIBLE findings. Every candidate below was examined and dropped,
because it is behaviour the contract requires or it lies outside the diff. They are listed so the
human can weigh them for the 2.8.4 notes.

- **Several A records: no fallback after the first.** The new dial resolves through the
  AF_INET `connect`, which uses only the first IPv4 address. The old `create_connection` walked every
  address, of either family. A TLS client whose server name's first A record is unreachable now
  fails where it once fell through. The cleartext client has always behaved this way, and R2
  requires the TLS client to reach the same endpoint, so this is not a defect. It could not be
  driven here (sandbox DNS). It may deserve a line in the 2.8.4 notes.
- **IPv6 literal or AAAA-only name: the message says "Could not resolve host".** `hs client -H ::1`
  now reports `HostAddressInfo: Could not resolve host '::1'` (exit 5). That is the cleartext message
  R4 asks for. A more specific message is a stated non-goal.
- **`hs server --bind ::1` does not exit.** The listener child dies with `socket.gaierror`, and the
  parent was still alive when the 8 s and 20 s kill timers fired, on both transports. This is
  pre-existing, in code the diff does not touch. The seed's claim that an IPv6 bind address "fails"
  is accurate. Whoever shapes that seed may want to record that the server hangs rather than exits.
- **Timeouts and blocking mode.** `setblocking(True)` overrides `socket.getdefaulttimeout()`, which
  `create_connection` would have honoured. Nothing in `src/` or `tests/` sets a default timeout, and
  the cleartext client does the same, so behaviour is unchanged. A dial to an unroutable IPv4
  address could not be exercised (the sandbox returns EPERM for both transports alike).

## Human-gate triggers

- Not triggered. The diff touches `core/queue.py` (high blast radius), but there is no CONFIRMED
  finding there or anywhere else. TLS posture is unchanged: TLS is on by default, verification,
  `servername` and fingerprint pinning are intact, the auth challenge is untouched, and there is
  still no mTLS.

## Optional completeness sub-pass (separate reviewer; may see TECH.md)

- Not run (not requested).
