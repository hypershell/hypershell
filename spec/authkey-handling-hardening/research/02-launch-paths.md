# 02 — The four launch paths and how each can hand over the key

Scope: the post-zone launchers on `develop` (`02b292a`), and what each needs in order to deliver
the key off the command line. Read-only.

- **`RemoteCluster`** (`--launcher`, `--mpi` = `launcher='mpirun'`; `cluster/remote.py`).
  - One `Popen(client_argv, env={**os.environ, **load_task_env()})`.
  - An explicit env mapping already exists, so the key is one more entry in it.
- **`AutoScalingCluster` / `AutoScaler`** (`cluster/remote.py`).
  - The argv is built once in `AutoScalingCluster.__init__`. `AutoScaler.scale()` runs
    `Popen(self.launcher, …, env={**os.environ, **load_task_env()})` on every scale-up, for both
    policies.
  - The default autoscale launcher is empty, which gives a direct local `hs client`.
  - `tests/test_autoscale.py` builds `AutoScaler(launcher=['hs','client'])` and never calls
    `scale()`.
- **`SSHCluster`** (`cluster/ssh.py`).
  - One `Popen` per host, with no `env=` and no `stdin=` (ssh inherits both).
  - `ssh` joins the words after the host and the remote login shell re-parses them. That is why
    today's code quotes `-t` and `--zone` by hand.
  - `--env` (`export_env`) prepends `KEY="value"` words from `compile_env()`.
  - Standard environment forwarding over ssh needs server-side `AcceptEnv`, which is not
    portable. The ssh channel's standard input is portable.
  - A POSIX `sh -c` preamble (`IFS= read -r VAR; export VAR; exec …`) reads one line into the
    variable the client already reads, and works under any login shell.
  - `-T`, placed after user arguments, overrides `-t`/`-tt` and `RequestTTY` in `ssh_config`
    (OpenSSH applies the last command-line flag, and the command line beats the config).
- **`LocalCluster`** (`cluster/local.py`). In-process `ClientThread(auth=…)`. No subprocess, so it
  is out of scope (GOAL).
- **Shared shape.** The three subprocess paths build identical client option lists, three times
  (AGENTS.md: "no shared client-argv builder"). One function can own the options, the key handoff
  and the redacted log line. The callers keep the key, the template choice (JSON mode →
  `DEFAULT_TEMPLATE`), the advertised host and the launcher words.
- **Launcher environment pass-through.**
  - `srun` (default `--export=ALL`), MPICH/Hydra, Intel MPI and Flux pass the caller's
    environment to tasks.
  - Open MPI's `mpirun` passes `OMPI_*` and any variable named by `-x` or the MCA parameter
    `mca_base_env_list`. It can be set as `OMPI_MCA_mca_base_env_list`, which keeps the name off
    the command line too.
  - That parameter is documented for Open MPI 4 and not checked on 5 (PLAN §5 Q1).
- **2.8.4 delta.** `git diff 2.8.4 02b292a -- src/hypershell/cluster/{remote,ssh}.py` shows only
  `zone` and `from_json`/`client_template` additions in these blocks. The structure is otherwise
  identical, so a new-module design ports to 2.8 as a new file plus three call-site edits.
