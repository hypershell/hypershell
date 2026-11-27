---
status: unshaped
kind: feature
appetite: medium
target: 2.10.0
lane: public
---

# Task signing: clients run only what a trusted submitter signed

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.
>
> **Pair:** `issues/untrusted-server-client-sandbox.md`. Both seeds address the same threat: a
> compromised server. This one stops the server from authoring tasks; the sandbox seed bounds the
> damage from tasks that were legitimately signed and still turn out harmful. Either can ship first.
>
> **Depends on:** a queue transport that carries data, not pickled objects. Landed in #123
> (`spec/queue-wire-codec/`), shipping in 2.9.0a10: the transport can no longer run code in a
> client, so signing can hold against a compromised server.

## Problem

A client executes whatever command the server hands it. Whoever controls the server process or its
database can run arbitrary code, as the user, on every connected client. Neither credential HyperShell
has today addresses this:

- **The authkey** is a symmetric secret held by the server and by every client. Anyone who holds it
  can forge tasks, the server included.
- **TLS** proves the server's identity to the client. The server is the party in question, so TLS
  secures the connection and says nothing about where a task came from.

Nothing in a task row lets a client check that the person who submitted the work wrote the command.

## Outcome / vision

The submitter signs each task with a key the server never holds. The server stores and relays the
signature untouched. A client configured to require signatures verifies each task against public keys
it holds locally and refuses anything that fails. A compromised server can still drop, delay or
reorder work. It can't make up a command, and it can replay a signed one only within limits the
submitter set.

## Design settled with Geoffrey (2026-11-22)

- **Key type:** Ed25519 in OpenSSH format, so an existing SSH key works. `cryptography` loads and
  signs with it. Signing through `ssh-agent` is a stretch goal.
- **Trust file:** client-local, in the style of OpenSSH's `allowed_signers`: one key ID and public key
  per line. Only whoever runs the client chooses it, by flag or local config. The server never sends
  it and can't override it.
- **Signed payload:** a canonical, length-prefixed encoding of
  - the namespace string `hypershell-task-v1`, so a signature can't be reused for anything else;
  - format version;
  - key ID;
  - the task ID assigned at submission;
  - `args`;
  - expiry time (`not_after`);
  - an optional audience tag naming the clients or site the task is meant for.
- **Sign what is stored.** The signature covers `args` as submitted. A client-side `-t/--template`
  applies after verification, so the template stays the client's choice and sits outside the
  signature.
- **On the wire:** key ID (8-byte fingerprint), `not_after`, and the 64-byte signature, about 80 bytes
  raw, in a dedicated task column. It does not go in `tags`: tags are meant to be edited after
  submission, and the signature is first-class and immutable. The client can verify only fields it
  receives, so the column must also carry the format version, the audience tag when one is set, and
  the signed task ID. A retry row gets a new `id` (`Task.next`'s retry path,
  `__schedule_next_failed_tasks`, creates it with `previous_id=task.id`), so the row's own `id` won't match the signed one. That makes the column
  about 100 bytes, plus the audience.
- **Cost:** Ed25519 verification takes microseconds per task and does not affect throughput.
- **Fail closed:** under `--require-signed`, a missing trust file stops the client from starting, and
  a bad task is refused with its own status.
- **Signing again through `hs update --sign`** (Geoffrey, 2026-11-22). A task whose signature has
  expired, whose signing key has been rotated, or whose command must change gets a new signature
  from `hs update`, run where the signing key is. The trap is that `hs update` reads rows from the
  database, which is the party signing exists to distrust. Signing whatever `args` it finds would
  give a tampered command a valid signature. So `--sign` signs only what the signer can vouch for:
  - **A new command:** `hs update <filter> args='…' --sign` signs the value given on the command
    line, never one read back, and writes `args` and the signature in one statement.
  - **Same command, new signature:** a row whose current signature still verifies against the
    signer's own trust file, ignoring expiry, gets a fresh one (new `not_after`, or the new key).
    That a valid signature exists proves `args` is what was signed. A row that was never signed, or
    whose signature fails, is refused and named, and the signer must give its `args` explicitly.
  - The write is conditional on `args` still holding the value that was verified or given, through
    the contended-write path every `hs update` write already uses. Otherwise another writer can
    change `args` between the check and the write.
  - The new signature binds the signed task ID carried in the signature column, so it covers retry
    rows as the original did.
- **Open for shaping: what a refused task becomes.** There is no status enum. A row's state comes from
  its columns, and today's "never ran" outcomes are `exit_status` sentinels below `-1000`
  (`TASK_TEMPLATE_ERROR`, `TASK_RESOURCE_ERROR`, `client.py:717`). A refused task needs a terminal
  state that no failure or retry query picks up. Otherwise it is retried until its attempts run out
  and then stalls its group (as `-1001`/`-1002` did before #119, `spec/update-client-retry/GOAL.md`), or, if left
  unscheduled, it is dispatched to client after client indefinitely. Either it gets a reserved
  sentinel excluded from those queries, or R4's "never as a task exit status" needs a new column.

## Sketch of the acceptance criteria

- **R1** — When a signing key is configured, submission SHALL sign each task over the payload above
  and store the signature with the task.
- **R2** — The server and the database SHALL carry the signature unchanged. No server code path SHALL
  create or alter a signature.
- **R3** — When `--require-signed` is set, the client SHALL refuse to start without a readable trust
  file.
- **R4** — When `--require-signed` is set, the client SHALL refuse any task whose signature is
  missing, fails verification against the trust file, is past `not_after`, or names an audience the
  client is not part of. The refusal SHALL be recorded as a distinct status, never as a task exit
  status.
- **R5** — A task whose `args` changed after signing SHALL be refused.
- **R6** — A server-side retry SHALL carry the original signature and remain verifiable (see Notes).
- **R7** — Without `--require-signed`, behaviour SHALL be unchanged. Unsigned tasks run as they do
  today.
- **R8** — The docs SHALL state what signing protects against and what it does not, including the
  `hs cluster` case below.
- **R9** — `hs update --sign` SHALL sign a command given in the same invocation, or sign again a row
  whose current signature verifies against the signer's trust file (expiry aside). It SHALL refuse,
  and name, any selected row that meets neither condition, and SHALL never sign `args` it only read
  from the database.

## Notes

- **Retries.** The server can't sign a retry row. The signature binds the original task ID and `args`,
  and retry rows copy it, so the server can re-run a command the submitter really signed. The expiry
  time, the audience tag and a per-client cache of signed IDs already seen bound that. Open question
  for shaping: sign `max_attempts` into the payload so a client can cap executions per signed ID, or
  accept replay inside the expiry window. Eliminating replay entirely needs a shared record of
  executed tasks, which is out of scope.
- **`hs cluster`.** The submitter and server are one process, so the key sits next to the server and
  signing adds little. Signing pays off when submission is separate: sign on a trusted host, run
  `hs server` on a host that never sees the key. The docs must say this, or users will assume
  `hs cluster` is protected.
- **Database tampering** fails the same check, with no extra work.
- **Dependency.** `cryptography` is already a hard dependency (`>=3.4`, kept low for RHEL/EPEL, used
  by `core/tls.py`), so no `[signing]` extra is needed. Ed25519 and `load_ssh_private_key` are both
  in the 3.x line; confirm the floor covers OpenSSH-format Ed25519 keys before relying on it.
- **Schema.** On every backend the upgrade step (`data/upgrade.py`) adds a missing column if it is
  nullable, so a nullable signature column needs no hand-written migration. Check the in-flight
  backend seeds (Turso) before shaping.
- **Every path a task takes.** R2 covers more than the server's writeback: a column declared in
  `Task.columns`, so it survives `serialize_tasks` and `Entity.from_json`; the live queue
  (`hs submit -q` into a `--no-db` server); and a relay, which stores each task as JSON and clears
  `LOCAL_STAMP_COLUMNS` before re-serving it to its site (`relay/store.py`). The signature must not
  be on that cleared list.
- **`hs update` on `args`** without `--sign` changes a signed row, and R5 refuses it at the client.
  The docs should say that editing a signed task's command without `--sign` makes it unrunnable
  under `--require-signed`.
- **Wire format, open.** SSHSIG (`ssh-keygen -Y sign`) would give interop with OpenSSH tooling. Raw
  Ed25519 over the canonical payload is smaller and faster to verify. The namespace and the
  `allowed_signers`-style trust file already mirror SSHSIG's semantics; lean raw.
- **Line references** in the pair seed come from the `templating-and-remote-env-contract` seed.
  Re-anchor them against current `develop`.
- Found by: design discussion with Geoffrey while scoping the deployment guide, 2026-11-22.
