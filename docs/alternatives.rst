.. _alternatives:

How HyperShell Compares
=======================

|

*HyperShell* belongs to a specific, old, and honorable genre: you hand it a flat list of
shell commands and it runs them — fast, fault-tolerantly, and across whatever hardware you
happen to have. That is the whole abstraction. There is no workflow language to learn, no
Python API you are required to call, no directed-acyclic graph to declare. If you can write
the command on one line of a shell, you can submit it. This is the same contract as
``xargs`` and `GNU Parallel <https://www.gnu.org/software/parallel/>`_ — and it is a
deliberate choice, not a missing feature.

The "workflow tools" space is crowded and the names get used loosely, so it helps to be
precise about *what kind of thing* each tool is before arguing about which is better. Two
axes separate them cleanly:

**Interface — how you describe the work.**
    A **shell command** (``xargs``, GNU Parallel, the HPC bundlers, *HyperShell*); a
    **workflow DSL / rule file** (Snakemake, Nextflow, Makeflow, Pegasus, HTCondor submit
    files); or an **embedded programming API** (Dask, Ray, Parsl, Balsam, FireWorks,
    RADICAL-Pilot, TaskVine). The first meets you where a shell script already lives; the
    other two ask you to author your work inside their model.

**Control plane — what actually coordinates and remembers the work.**
    A **single process** (``xargs``, GNU Parallel, the bundlers); a **server with
    workers** and some form of persisted state (*HyperShell*, HyperQueue, Balsam, Merlin);
    a **DAG/graph engine** (Snakemake, Nextflow, Pegasus, Dask, Ray); or a **standing pool
    manager / pilot system** (HTCondor, OSPool, RADICAL-Pilot). Orthogonally, the *state*
    ranges from none, to a log file, to a full queryable database in the loop.

On both axes *HyperShell* occupies one clear cell: a **shell-command interface** over a
**server-plus-elastic-clients control plane** with a **SQL database in the loop** and a
**flat, independent task model**. A server pulls pending tasks from SQLite or PostgreSQL,
bundles them, and pushes them over an (encrypted, authenticated) TCP queue to clients that
pull work, spawn shell subprocesses, and return results; the server writes exit status,
timing, and captured output back to the database. Clients come and go — scaling out across
nodes over SSH (with ``a[00-32].cluster`` nodelist expansion) or under MPI/Slurm launchers,
and scaling back down to zero. Fault tolerance is concrete, not aspirational — because every
task's state lives in the database, nothing in flight is lost. A server restart recovers
interrupted tasks; a client that stops sending heartbeats is evicted and its in-flight tasks
are reverted and rerun on another client; and failures are retried automatically up to a
configurable limit. There is no fragile run to babysit: the database is the source of truth,
and the cluster simply converges on completion. See :ref:`getting started <getting_started>`
for the mechanics.

The argument this page makes is simple: **most many-task work is flat.** A parameter sweep,
a million files to convert, an ensemble of independent simulations — these have no
dependency graph worth the name. For that enormous class of work, paying the conceptual and
operational tax of a DAG engine, a workflow DSL, or a programming API buys you nothing you
needed. *HyperShell* gives you the database-in-the-loop robustness and elastic scale of the
"serious" systems while keeping the ``xargs``-simple shell interface — and it does so as a
single, pip-installable, cross-platform Python package you can also embed as a library.

In our own scaling tests, *HyperShell* has run oversubscribed across all 1,000 nodes of
`Anvil <https://www.rcac.purdue.edu/anvil>`_ (Purdue) — roughly 3,600 connected clients
driving on the order of 280,000 concurrent task executors — and has been exercised on
`Summit <https://www.olcf.ornl.gov/olcf-resources/compute-systems/summit/>`_ (OLCF) and
`Aurora <https://www.alcf.anl.gov/aurora>`_ (ALCF). It scales this way precisely because
**clients never touch the database** — only the server does, aggregating task hand-out and
result write-back into large batched transactions and shipping work to clients in *bundles*.
A single, batching writer means even the embedded SQLite default sustains very high
throughput; PostgreSQL is there for shared, multi-user, or networked deployments, not because
scale demands it.

None of this makes *HyperShell* the right tool for *every* job, and the sections below try
hard to say where each alternative genuinely wins. Where a tool is simply operating in a
different regime, we say so; where it overlaps *HyperShell* directly, we draw the line
carefully. *HyperShell* is also honest about its own trade-offs. It has **no full DAG
engine** — but it does offer a lightweight **task-group** lever: tasks carry a group number
and the server runs them in ordered phases (every task in one group finishes before the next
group begins), which covers the staged pipelines most workloads actually are, with no DSL or
API. For genuinely arbitrary dependency graphs, compose *HyperShell* *under* one of the
workflow managers below. Its running server is also a single coordination point — mitigated,
as above, by database-persisted state that self-heals across restarts and client failures.

-------------------

The Baseline: The Batch Scheduler Itself
----------------------------------------

|

Before any of these tools, there is the cluster's own scheduler. Every reader will
mentally compare against it, and the entire genre exists because of one anti-pattern.

Slurm
^^^^^

The naive way to run 100,000 tasks on an HPC system is to submit 100,000 jobs. Every site
discourages this: it floods the scheduler's queue, blows through per-user submission limits,
and buries your real work under scheduling overhead. `Slurm <https://slurm.schedmd.com/>`_
job arrays (``sbatch --array``) and ``srun --multi-prog`` soften the blow by packing many
tasks into fewer scheduler entries, but arrays are still coarse (one array element per task,
bounded by ``MaxArraySize``), heterogeneous per-task resources are awkward, and there is no
persistent, queryable record of individual task outcomes beyond accounting logs and your own
output files.

This is precisely the gap the many-task genre fills: acquire an allocation (or a set of
hosts) *once*, then schedule your thousands of tasks *inside* it, out of the batch
scheduler's sight. *HyperShell* runs happily under a single Slurm allocation (as an MPI or
``srun`` launcher target) and turns that allocation into a high-throughput task engine with
a real database of what ran, when, and how it exited — the thing job arrays never gave you.

Flux
^^^^

`Flux <https://flux-framework.org/>`_ (LLNL) is the modern answer at the scheduler layer
itself: a fully hierarchical resource manager whose instances can nest, so a Flux allocation
can spawn a child Flux instance that schedules many sub-jobs with first-class many-task
support (``flux submit``, ``flux bulksubmit``). Flux is genuinely excellent, and where it is
installed it removes much of the "don't flood the scheduler" problem at the source — several
tools on this page (Merlin, RADICAL-Pilot, Nextflow, Makeflow) can target it directly.

The distinction from *HyperShell* is one of role. Flux *is* the scheduler — a systems-level
resource manager a center deploys and operates. *HyperShell* is user-space software you run
on top of whatever scheduler exists (Flux, Slurm, PBS, or none at all — just SSH hosts),
with a shell-command interface and a portable SQL task catalog that travels with you across
sites regardless of the local scheduler. The two compose cleanly: run *HyperShell* inside a
Flux allocation and you get Flux's placement plus *HyperShell*'s catalog and elasticity.

-------------------

The Bundler Family
------------------

|

This is *HyperShell*'s own genre — the flat list of shell commands — and its closest
relatives in spirit. What *HyperShell* adds is a persistent control plane: a database and a
server that outlive any single run or allocation, plus clients that elastically join and
leave.

GNU Parallel (and ``xargs``)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^

`GNU Parallel <https://www.gnu.org/software/parallel/>`_ is the definitive, battle-tested
anchor of the genre and the tool closest to *HyperShell* in feel. Ole Tange's single Perl
script (GPLv3, first released 2007) turns any pipeline into parallel work in one line, with
input expansion far richer than *HyperShell* aims to replicate: replacement strings
(``{}``, ``{.}``, ``{/}``, ``{#}``), Cartesian products and zips across ``:::`` / ``::::``
sources, ``--pipe`` / ``--pipe-part`` streaming, resource-aware admission (``--load``,
``--memfree``, ``--noswap``), deterministic output ordering, and genuinely usable multi-host
fan-out over SSH with input/output staging. For a single machine or a handful of known
hosts, it is superb and needs zero infrastructure. ``xargs`` is the minimalist ancestor —
``-P`` for parallelism, ``-0`` for safe input — and remains the right reach for the simplest
cases.

The divide is the control plane, not the interface. GNU Parallel is one long-lived
controller process that *pushes* jobs over SSH; its persistence is an optional flat
``--joblog`` file used for ``--resume`` / ``--retry-failed`` (recovery that does, usefully,
work across re-runs). It has no server that clients *connect to*, no database, no
cross-run queryable catalog, no ``key:value`` task tags, and no elastic pool that scales to
zero. *HyperShell* keeps the same shell-command front door but replaces the controller with
a server-plus-database: tasks live in SQL with full history and tags, retries are
server-managed, and clients self-register and pull work rather than being pushed to. In
short — GNU Parallel for instant, ad-hoc, single-controller work; *HyperShell* when you want
that same interface backed by a persistent, elastic, queryable system.

HPC task bundlers: ParaFly, TaskFarmer, TACC Launcher, disBatch
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

A cluster of tools consumes a text file of independent shell commands and spreads it across
the cores of one batch allocation: **ParaFly** (a BSD-licensed OpenMP wrapper from the
Trinity RNA-seq project, with per-command retry and a ``.completed`` resume file);
**TaskFarmer** (`NERSC <https://docs.nersc.gov/jobs/workflow/taskfarmer/>`_'s shell-based
server/worker manager, checkpoint-and-resubmit recovery, sustaining ~5–10 tasks/second);
**TACC Launcher** (MIT-licensed, ``paramrun`` across a Slurm/PBS node list with
dynamic/interleaved/block scheduling); and **disBatch**
(`Flatiron Institute <https://github.com/flatironinstitute/disBatch>`_'s Python task-farmer
with a per-task status file, ``-r``/``-R`` resume-and-retry, coarse in-file ``BARRIER``
primitives, and Slurm/SSH backends).

These are excellent at exactly what they do, and their radical simplicity — no server, no
database, nothing to operate — is a real virtue. Their shared limitation is lifetime and
scope: each lives and dies inside a *single* allocation, with state kept in run-local side
files (``.completed``, ``.tfin``, ``_status.txt``) rather than a cross-run catalog, and
their workers are the fixed node set of the current job. *HyperShell* shares the same flat
front door but runs a persistent server backed by a SQL database whose task catalog spans
many runs and allocations, with history, tags, retries, and a ``task``/``submit``
management CLI (``search``/``info``/``update``/``wait``) — and its clients elastically join
and leave rather than being pinned to one job. The bundlers are the right choice when one
allocation and zero infrastructure is all you need; *HyperShell* when the workload outgrows
a single job or you want a durable, queryable record.

-------------------

HyperQueue — the Closest Overlap
--------------------------------

|

Of everything on this page, `HyperQueue <https://it4innovations.github.io/hyperqueue/>`_
(``hq``), from `IT4Innovations <https://www.it4i.cz/en>`_ (the Czech national supercomputing
center), overlaps *HyperShell* the most. It is worth stating that plainly and generously:
the two projects arrived independently at nearly the same architecture. Both place a
persistent **server** on a login node and elastic **workers** on compute nodes; both take
shell commands and run them at high throughput; both exist to pack many tasks into
allocations without hammering the batch scheduler. This is convergent design on a genuinely
good idea, and HyperQueue is a well-engineered, peer-reviewed
(`SoftwareX 2024 <https://doi.org/10.1016/j.softx.2024.101814>`_) piece of software.

The shared surface area is large: server + workers, a work-stealing scheduler, elastic
worker pools, output handling designed for parallel filesystems, a "don't submit small jobs
to Slurm" philosophy, and — since *HyperShell* v2.8.0 — a TLS-encrypted, authenticated queue
by default on both sides. Both are also built to *amortize* per-task overhead rather than pay
it per task — *HyperShell* by dispatching and updating tasks in bundles, HyperQueue via its
low-overhead work-stealing core — which is why both scale to enormous task counts. Where
HyperQueue genuinely earns its keep and goes beyond *HyperShell*:

* **A single static Rust binary.** No interpreter, no shared libraries, no service to
  install — ``scp`` one file onto a login node and run. This is excellent HPC deployment
  ergonomics, and it deserves credit without qualification. (We admire it enough that it is
  a direction worth respecting.)
* **Batch-scheduler-native auto-allocation.** ``hq`` speaks Slurm/PBS directly — submitting,
  sizing, and reclaiming its *own* worker allocations against pending demand with no glue
  code. If your world is Slurm/PBS, that is genuinely convenient out of the box (see the
  counterpoint below on *HyperShell*'s deliberately scheduler-agnostic ``--launcher``).
* **A fine-grained resource model.** Fractional GPUs, indexed/non-fungible resources,
  resource variants, and NUMA-aware placement (``compact``/``compact!``/``scatter``) exceed
  what most many-task tools offer.
* **Output streaming** into a compact log so the file count is independent of the task
  count, and **true arbitrary-DAG dependencies** via its Python API (``deps=[...]``) — beyond
  *HyperShell*'s linear task-group phases.

Where *HyperShell* earns *its* keep, and where the two genuinely differ:

* **State model.** *HyperShell* keeps a **SQL database in the loop** (SQLite or PostgreSQL):
  a persistent, directly SQL-queryable task catalog with cross-run history, ``key:value``
  tags, and a rich ``task``/``submit`` CLI. HyperQueue keeps state in the running server's
  memory and persists it *optionally* to an append-only binary **journal**; you inspect it
  by asking the live server over the CLI (with JSON output the docs mark unstable), not by
  querying a database. For workflows where the task catalog *is* the deliverable — auditing,
  reporting, re-querying months later — that difference matters.
* **Interface.** *HyperShell* reads literal shell-command strings from stdin/files with
  in-string ``{}`` / ``{0}`` template expansion and tags — the ``xargs``/GNU-Parallel
  ergonomic. HyperQueue's unit of work is a structured program invocation authored via
  CLI/TOML/Python, parameterized through environment variables (``HQ_TASK_ID``,
  ``HQ_ENTRY``) rather than in-command placeholders; its dependency (DAG) power lives only in
  the Python API.
* **Portability & embedding.** *HyperShell* is pure Python — pip-installable, embeddable as
  a library, and cross-platform (its founding use case was a Linux server driving **Windows**
  clients in cluster VMs). HyperQueue is a Linux/HPC-oriented Rust binary (multi-arch:
  x86-64, AArch64, PowerPC) and is not a Python library you embed.
* **A scheduler-shaped hole, not scheduler-specific code.** *HyperShell* can absolutely
  submit its own workers — but rather than bake in Slurm/PBS awareness, it exposes a
  ``--launcher`` seam and spawns its clients through *whatever* you name: ``mpirun``,
  ``srun``, ``ssh``, a site wrapper, or anything else that behaves like a launcher. This is
  less turnkey than HyperQueue's native auto-allocation, and deliberately so: it is the more
  flexible abstraction — it composes with any scheduler (or none at all) and keeps
  *HyperShell* free of coupled code forever chasing scheduler version changes. Offering a
  scheduler-*shaped hole* rather than scheduler-*specific plumbing* has been a years-long
  design commitment, not an omission.

On the perennial "but it's Rust vs. Python" point, we want to be precise rather than
defensive. The single static binary is a real advantage and we will not pretend otherwise.
But per-task *scheduling overhead* is not the axis most real workloads live or die on: it is
negligible relative to task granularity. Nobody schedules ``sleep 1`` a hundred thousand
times in anger. Real many-task work is seconds-to-hours per task, where a millisecond of
dispatch overhead is lost in the noise — which is why *HyperShell*, in pure Python, has run
oversubscribed across all 1,000 nodes of Anvil at ~3,600 clients and ~280,000 executors.
The language is a deployment and ecosystem choice; the throughput that matters is a
non-issue for the workloads either tool is actually for.

The two tools simply optimize for different points in the same design space: HyperQueue for
scheduler-driven allocation and fine-grained, dependency-aware resource matching from a
zero-dependency binary; *HyperShell* for a persistent, SQL-queryable, template-driven
shell-command catalog that installs with ``pip`` and embeds in Python. Both are good, and the
degree of overlap is best read as a sign that the underlying idea — a server with elastic
workers, feeding shell tasks in bundles — is the right one.

-------------------

Database-in-the-Loop Cousins
----------------------------

|

*HyperShell* is not unique in putting a database in the loop — several serious systems keep
an authoritative, queryable task store. What distinguishes *HyperShell* is not *having* a
database but keeping a **lightweight, self-contained** one: an embedded SQLite file (or a
PostgreSQL URL) with **no external broker, service, or OAuth** to stand up. These three
cousins are the closest in architecture, and each is the right tool for a workload
*HyperShell* deliberately does not target.

Balsam
^^^^^^

`Balsam <https://balsam.readthedocs.io/>`_ (Argonne Leadership Computing Facility) is the
nearest architectural relative: a **PostgreSQL-backed** job database with pilot "launchers"
that pull runnable jobs and execute them inside allocations — the same database-in-the-loop,
pull-based shape as *HyperShell*. Its differences are deliberate and substantial. Balsam's
unit of work is a Python ``ApplicationDefinition`` class (a Jinja2 command template plus
typed parameters and ``preprocess``/``postprocess`` hooks) that you ``sync()`` to a named
site; its control plane is a **central, always-on web service** (FastAPI + PostgreSQL +
Redis + OAuth) built for **multi-site federation** — triggering analysis across several
supercomputers from one service is a first-class goal, demonstrated across DOE machines
(current integrations target Aurora, Polaris, and Perlmutter). It models true inter-job
**dependencies** (``parent_ids``) and per-job data staging. (The Balsam2 line remains
labeled Alpha, though the repository is not dormant.)

*HyperShell* trades all of that federation and dependency machinery for radical
self-containment: a raw shell-command interface (no per-application Python class to
register), a server that pulls from its *own* SQLite/PostgreSQL database with no Redis,
Globus, or OAuth, and elasticity in the *clients* rather than in a remotely-managed batch
queue. Balsam is the right tool for dependency-aware, data-staged, remotely-federated
campaigns across facilities; *HyperShell* for throwing a very large flat stream of shell
commands at whatever nodes you hold, with minimal infrastructure.

FireWorks
^^^^^^^^^

`FireWorks <https://materialsproject.github.io/fireworks/>`_ (LBNL, tied to the Materials
Project) is **MongoDB-backed** and Python-object-centric: a workflow is a DAG of
``FireWorks``, each holding Python ``Firetasks`` (including ``ScriptTask`` for shell
commands), stored in a central "LaunchPad" that distributed "rockets" pull from. Its
standout features are genuine: **dynamic workflows** (a task can rewrite the running graph),
**duplicate detection** across overlapping campaigns, and strong **provenance** — all
proven at scale in materials science (atomate/atomate2). It is DB-in-the-loop like
*HyperShell*, but the database is Mongo, the unit of work is a Python class inside a DAG, and
the value proposition is dependencies and provenance. *HyperShell* is SQL-backed and
language-agnostic, flat by design — choose FireWorks when the workflow's structure and
reproducibility matter as much as throughput; *HyperShell* when you need maximum-throughput
dispatch of independent shell commands with a simple SQL catalog.

Merlin
^^^^^^

`Merlin <https://merlin.readthedocs.io/>`_ (LLNL) is **broker-in-the-loop**: a Celery
application that expands a Maestro-style **YAML study spec** (with built-in parameter/sample
generation) into a task DAG enqueued on a **RabbitMQ or Redis broker**, with a separate
results backend, executed by Celery workers placed on allocations. Its ensemble
ergonomics — turning a parameter space plus a sampling command into hundreds of thousands of
coordinated ML+HPC runs — are a first-class, well-designed strength, and reusing Celery
inherits mature retry/routing. The cost is moving parts: an external broker *and* a results
backend, and a YAML study to author, versus piping commands into a self-contained process.
*HyperShell* dispatches and records everything from one server against one SQL database, with
a language-agnostic command interface and a deliberately flat task model. Merlin is squarely
right for sampled, multi-stage ML ensembles; *HyperShell* for self-contained, high-throughput
flat command lists.

-------------------

Python-Native Engines
---------------------

|

Dask and Ray are enormously popular and frequently mentioned in the same breath as "parallel
computing," but they operate in a fundamentally different regime: they are **embedded Python
frameworks** whose unit of work is a Python object, not a language-agnostic shell command.

Dask
^^^^

`Dask <https://www.dask.org/>`_ scales the NumPy/pandas/scikit-learn ecosystem to
larger-than-memory and multi-node data through lazy task graphs (``dask.delayed``), a
futures API, and parallel collections (array/dataframe/bag), executed by a distributed
scheduler with locality-aware placement and in-memory data flow between workers. It is
best-in-class for **dependent, data-parallel analytics and dynamic graphs** in Python, with
a deep HPC/cloud deployment story (``dask-jobqueue``, Kubernetes, adaptive autoscaling) and
strong NumFOCUS governance. Its state is in-memory and ephemeral — losing the scheduler
loses graph state — and its workers must share the client's Python environment (objects are
pickled). That is exactly the opposite of *HyperShell*'s design: language-agnostic shell
commands, independent by construction, persisted in a durable SQL catalog that survives
restarts. The two are complementary — you could even run Dask workers *as* HyperShell tasks.

Ray
^^^

`Ray <https://www.ray.io/>`_ is a distributed-Python application framework built on remote
tasks, stateful **actors**, and a shared in-memory **object store**, with a rich ML ecosystem
(Tune, Train, Serve, RLlib) and demonstrated throughput beyond 1.8 million tasks/second. It
is best-in-class for **tightly-coupled, stateful distributed Python** — reinforcement
learning, distributed training, model serving — where actors and zero-copy in-memory data
sharing are the point. (A bare ``ray.init()`` runs on one machine; the operational weight is
a distributed-cluster concern.) As with Dask, the natural unit is a Python callable, state
lives in memory (object store + GCS) for the job's lifetime, and there is no persistent SQL
task catalog by design. *HyperShell* targets the other end: opaque, language-agnostic shell
commands, flat and independent, with a durable database of record. Ray for stateful,
data-sharing Python apps; *HyperShell* for high-throughput independent shell commands with a
persistent record.

-------------------

Parsl and Globus Compute
------------------------

|

`Parsl <https://parsl.readthedocs.io/>`_ (with its companion FaaS,
`Globus Compute <https://www.globus.org/compute>`_, formerly funcX) is a powerful,
well-funded, peer-reviewed effort from the University of Chicago and Argonne, and it runs
shell commands perfectly well (via ``@bash_app``). It deserves genuine credit: real DAGs
emerge naturally from ordinary Python data flow (an ``AppFuture`` passed to another app
creates a dependency), it is deeply portable across schedulers and clouds via its
Provider/Launcher abstraction, and Globus Compute adds a secure, managed, fire-and-forget
function service across administrative domains.

Our reservation is about the *abstraction*, and we think it is a fair one. Parsl's unit of
work is a **decorated Python function**, and any non-trivial use requires writing a Python
driver program plus an executor/provider/block configuration that surfaces batch-scheduler
concepts (``nodes_per_block``, launchers, ``init/min/max_blocks``, ``parallelism``) back to
the user as code to tune per site. That is real power for dependency-bearing, heterogeneous,
multi-resource workflows — and real overhead for a flat command sweep. Its durable state is
an *optional* SQLite monitoring sidecar, not a submit/search/update task catalog; there is no
``task search``/``wait`` CLI over a database of record.

But the sharpest distinction is architectural — and, importantly, it is *not* about scale.
Node count is a red herring here: the headline ~250,000-worker figure belonged to Parsl's
*Extreme-Scale* executor, which has since been **removed**; the ``HighThroughputExecutor``
that Globus Compute actually wraps has been run to tens of thousands of workers across a
couple thousand nodes — roughly on par with *HyperShell*. What differs is *how a task moves*.
Follow one through Globus Compute: the cloud service hands it — as an individual message — to a
per-user endpoint that the multi-user endpoint forks (and ``setuid``-execs) on your login
node; that endpoint wraps Parsl's ``HighThroughputExecutor``, whose provider brings up workers
as ordinary Slurm/PBS jobs. That provisioning path is fine, and comparable to *HyperShell*'s
launcher. The task *dispatch* path, though, is **per-task end to end**: each task is
individually serialized and shipped over AMQP (cloud→endpoint) and ZeroMQ (engine→interchange),
each result makes its own round-trip back, and each carries its own future bookkeeping. The
one hop that coalesces tasks at all is interchange→node, and only on demand — it ships just
enough already-serialized tasks to fill a node's idle worker slots (prefetch defaults to
zero), never an arbitrary bundle.

That per-task overhead — not node count — is the throughput ceiling, and Parsl's own figures
show it: even the removed 262,144-worker executor plateaued around ~1,200 tasks/second, and
today's ``HighThroughputExecutor`` lands in the same range; an order of magnitude more workers
did not raise the rate. This is precisely the seam *HyperShell*'s **aggregation** addresses —
it hands each client a large *bundle* of tasks in a single queue message and amortizes
completion write-back over one batched database transaction, so the fixed per-task costs are
paid once per bundle rather than once per task. Neither Parsl nor Globus Compute has an
equivalent semantic bundle or batched-transaction write-back.

None of this makes them opponents — the opposite, in fact, and this is the part worth
dwelling on. Globus's real superpower is not its executor at all; it is its **federated
identity and access layer** — the hard, unglamorous problem of *how do I get something running
on resources I don't administer*, solved across institutional boundaries. Globus Compute
brings that reach to task execution, and it is a genuine winner. *HyperShell*'s superpower is
the other half: a stateful, SQL-backed, high-throughput engine with a first-class UX.
Combined, you get the best of both — two patterns we have actually run (and shared with the
Globus community):

* **HyperShell drives, Globus Compute reaches.** A small ``gce-exec`` shim used as the
  ``--template`` sends each task through Globus's fabric to whatever endpoint you target, while
  a local *HyperShell* keeps the durable catalog, retries, and UX. At modest scale the
  per-task Globus overhead is a non-issue, and you gain federated reach for free.
* **Globus Compute reaches, HyperShell drives.** Invert it: a ``gce-launch`` shim used as a
  ``--launcher`` with ``--autoscaling`` lets Globus Compute provision workers on a remote
  cluster, and *HyperShell* becomes the task — an ephemeral client that connects back to your
  server and drains *bundled* work over the queue while the endpoint holds the node warm.
  Globus handles the cross-domain launch; *HyperShell*'s bundled queue and database do the
  high-throughput execution.

The inverted pattern is compact enough to show in full — Globus Compute launches a
*HyperShell* client *as* its task, and every task after that flows over *HyperShell*'s own
bundled queue:

.. code-block:: python

    from globus_compute_sdk import Executor

    def start_client():
        import hypershell as hs
        hs.run_client(address=('hs.my-site.edu', 50505), ...)

    with Executor(endpoint_id='...') as resource:
        resource.submit(start_client).result()

The division of labor is clean, and it is the through-line of this whole page: Globus Compute
for federated *reach*, *HyperShell* for stateful, high-throughput *execution*.

-------------------

Workflow-Definition Frameworks
------------------------------

|

Snakemake and Nextflow are the modern successors to GNU Make in scientific computing. They
are not really in *HyperShell*'s genre — they exist to express rich **dependencies** — but
they are the tools people most often ask about, and they **compose** with *HyperShell*
rather than compete with it. (For merely *staged* pipelines — all of phase A before phase B —
*HyperShell*'s task groups are often enough on their own; these tools earn their keep when the
dependency graph is genuinely a graph.)

Snakemake
^^^^^^^^^

`Snakemake <https://snakemake.readthedocs.io/>`_ models an analysis as a **file-driven DAG**
of rules: each rule declares input/output file patterns with ``{wildcard}`` placeholders, and
Snakemake infers the graph by matching one rule's outputs to another's inputs, rerunning only
what is out of date (Make-like, on filesystem state). The Snakefile is a Python superset;
reproducibility is first-class (per-rule conda/container environments, software-hash rerun
triggers); and the same workflow runs locally or across clusters/clouds via swappable
executor plugins. It is best-in-class for reproducible, dependency-heavy, heterogeneous
pipelines. The natural combination is obvious: Snakemake computes the dependency graph, and a
single embarrassingly-parallel *step* in that graph fans out through *HyperShell*.

Nextflow
^^^^^^^^

`Nextflow <https://www.nextflow.io/>`_ is a dataflow engine on the JVM: **processes** wired
by **channels** in a Groovy-based DSL2, with deep container integration, ``-resume``
caching, a huge community catalog (`nf-core <https://nf-co.re/>`_), and pluggable executors
spanning nearly every HPC scheduler and cloud batch service. It is superb at portable,
reproducible, containerized pipelines. But note *how* it dispatches on HPC: by default
Nextflow submits **one scheduler job per process invocation** (one ``sbatch``/``qsub`` per
task). At high task counts this is exactly the contention HPC centers warn against — NERSC's
`workflow guidance <https://docs.nersc.gov/jobs/workflow/>`_ steers flat, dependency-free
high-throughput work to tools like GNU Parallel for precisely this reason.

This is where *HyperShell* has a natural role beyond the flat regime. Because Nextflow's
executor layer is pluggable, *HyperShell* is well-positioned to serve as a **high-throughput
executor backend** beneath Nextflow — absorbing the many small tasks that should never become
individual Slurm jobs — while Nextflow handles the DAG, containers, and resume logic it does
best. (A ``hypershell-nextflow`` integration is on the :ref:`roadmap <roadmap>`.)

-------------------

Pilot and Many-Task HTC Frameworks
----------------------------------

|

These research-grade systems share *HyperShell*'s core motivation — hold an allocation and
schedule work *inside* it rather than submitting many small jobs — but bring heavier stacks
aimed at problems *HyperShell* leaves to the command or the filesystem.

RADICAL-Pilot and EnTK
^^^^^^^^^^^^^^^^^^^^^^

`RADICAL-Pilot <https://radicalpilot.readthedocs.io/>`_ (Rutgers/Brookhaven, part of
RADICAL-Cybertools) is a mature, peer-reviewed **pilot-job** framework: acquire one large
allocation, then run a client-plus-remote-agent architecture that schedules many
heterogeneous tasks — including MPI, multi-node, and GPU tasks, and (via its RAPTOR overlay)
hundreds of thousands of short Python functions — inside it. Ensemble Toolkit (EnTK) layers a
Pipeline/Stage/Task API on top for iterative simulation/analysis campaigns. Its strengths are
genuine: first-class heterogeneous MPI/GPU handling and characterized scalability on
leadership-class machines. It is also a Python API (not a shell stream), GNU/Linux-only,
carries more moving services (client interpreter, remote agent, ZeroMQ, RAPTOR
masters/workers), and — since dropping its MongoDB dependency — keeps state in filesystem
sandboxes and profile files rather than a queryable database. *HyperShell* delivers the same
"don't hammer Slurm" benefit through a much lighter shell-command-plus-SQL-server model, and
is pure-Python, cross-platform, and pip-installable. RADICAL is the heavier, research-grade
choice for heterogeneous MPI/GPU ensembles; *HyperShell* the lighter, database-in-the-loop
choice for flat throughput.

Makeflow and TaskVine (CCTools)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Notre Dame's `CCTools <https://cctools.readthedocs.io/>`_ pairs **Makeflow** — a Make/JX DAG
engine with a dozen pluggable backends (HTCondor, Slurm, Work Queue/TaskVine, Kubernetes,
cloud) — with **TaskVine**, the "third-generation" manager/worker framework whose defining
feature is **in-cluster data management**: it caches and replicates input data, software, and
intermediate results across workers by content-addressed name, with worker-to-worker peer
transfers, so tasks run where their data already lives. For data-intensive many-task work with
strong locality (HEP, bioinformatics, molecular dynamics), that is a real and rare capability.
*HyperShell* deliberately does **not** manage data locality — it dispatches shell tasks and
lets each subprocess touch the shared/remote filesystem — and it has no DAG engine (Makeflow's
role). What *HyperShell* offers instead is a SQL database of record for a flat command stream,
and a pure-Python (Apache-2.0) package rather than a C/GPLv2 toolkit with Python bindings.
TaskVine deserves clear credit for the data-management sophistication *HyperShell* intentionally
omits.

-------------------

HTCondor, the OSPool, and Pegasus
---------------------------------

|

The final group is a stack, and it is the one *HyperShell* is most often positioned
against — fairly, since it targets the same many-task, high-throughput regime. **HTCondor**
is the substrate; the **OSPool** is a national resource built on it; **Pegasus** is a
workflow manager layered on top. Together they represent the most capable, most operationally
demanding end of this landscape. *HyperShell*'s pitch here is not more capability — it is
**radically simpler operations and UX** for the large fraction of work that does not need the
full machinery.

HTCondor
^^^^^^^^

`HTCondor <https://htcondor.org/>`_ (UW–Madison, formerly Condor; Apache-2.0) is the
reference-grade distributed high-throughput computing system, purpose-built for the many-task
regime. Its **ClassAd matchmaking** is genuinely elegant and symmetric — both jobs and
machines advertise attributes, requirements, and preferences, and a central negotiator pairs
them — enabling policy-rich scheduling across heterogeneous, multi-owner, often opportunistic
resources. It has excellent durability (an accepted job is owned and run to completion across
crashes), first-class DAG workflows (**DAGMan**, with retries and pre/post scripts), pilot
overlays (GlideinWMS) that federate campus clusters, clouds, and grids into one pool, and it
scales to hundreds of thousands of execute cores.

That power comes with operational weight. HTCondor expects a set of persistent daemons
(``collector``, ``negotiator``, ``schedd``, ``startd``, plus ``condor_master``) installed,
configured, secured, and administered; you describe jobs as ClassAds in submit files and
think in universes; job state is spread across daemon queues, logs, and history files rather
than a single SQL catalog. *HyperShell* targets the same embarrassingly-parallel regime from
the opposite end: no standing daemons to run, a shell-command interface (not a submit-file
DSL) with ``{}`` templating and tags, and a server that pulls from a SQL database you can
query directly. For the flat regime it also holds its own on durability — task state is
persisted in the SQL catalog and recovered on restart, and a dead client's in-flight tasks
are reverted and rerun elsewhere, all without standing daemons. Where HTCondor is the right
answer when you *own and operate a durable, multi-user pool*, *HyperShell* is the direct
alternative when you just need to drive a large batch of shell commands across resources you
already hold — with a fraction of the setup.
(HTCondor has a real DAG layer in DAGMan; *HyperShell* has none by design — that is the trade.)

The OSPool and the OSG
^^^^^^^^^^^^^^^^^^^^^^

The `OSPool <https://osg-htc.org/services/ospool/>`_ — operated by the OSG Consortium under
`PATh <https://path-cc.io/>`_ (an NSF Cooperative Agreement) — is a free, national,
HTCondor-based pool that runs independent jobs **opportunistically** across compute donated
by dozens of contributing sites, provisioned by a GlideinWMS pilot overlay and HTCondor-CE
gateways. For a US-affiliated researcher with a large batch of single-core, short-to-medium,
modest-I/O, preemption-tolerant tasks, it is a remarkable proposition: burst to thousands of
concurrent jobs on national capacity at no cost, no allocation proposal required.

The key distinction is that the OSPool is **infrastructure you submit into**, not software
you deploy. You cannot "run the OSPool" on your own laptop, cloud account, or private
cluster; you must be eligible, you bring HTCondor jobs to an Access Point, and your work must
tolerate opportunistic preemption and explicit data staging. Its persistence and matchmaking
live inside HTCondor and central accounting (GRACC, the OSG's accounting service), not a
user-facing SQL table.

That data-staging story is the OSPool's traditional rough edge — and the one it is most
actively smoothing. The **Open Science Data Federation (OSDF)**, built on the
`Pelican Platform <https://pelicanplatform.org/>`_, layers a nation-wide data-distribution
network over the pool: repositories attach storage as *origins*, a content-delivery network of
*caches* (run by PATh and partners such as CHTC, ESnet, and Internet2) delivers data near
where jobs land, and it is all addressed through one global ``osdf://`` namespace that HTCondor
transfers on the job's behalf. It is not a mounted POSIX filesystem — it is a read-optimized,
caching data federation — but it is exactly the OSG's move to give opportunistic jobs the
*shared-data* convenience such pools have historically lacked, and it is maturing fast; it is
worth understanding as where OSPool data access is heading.

*HyperShell* is the complement: software you
fully own and run **anywhere** — including, in principle, on capacity you obtained *from*
OSPool/PATh — with a portable SQL task catalog and a shell-command interface. They are not
substitutes; a natural pattern is to use *HyperShell* to drive the resources, and the OSG to
help provide them.

Pegasus
^^^^^^^

`Pegasus <https://pegasus.isi.edu/>`_ (USC/ISI, with the HTCondor team; Apache-2.0) is the
most instructive contrast on this page, because understanding *what it is* clarifies what
*HyperShell* is not. Pegasus is a workflow **planner/compiler**, not a task runner. You
declare an **abstract**, resource-independent DAG through its Python API (``pegasus.api`` —
``Workflow``, ``Job``, ``File``), together with three **catalogs**: a Replica Catalog
(logical→physical file locations), a Transformation Catalog (executables per site), and a
Site Catalog (compute/storage resources). ``pegasus-plan`` (a Java planner) then compiles
that into a concrete **executable** workflow mapped onto specific sites and hands it to
**HTCondor DAGMan** to actually schedule and run. Pegasus itself executes nothing.

What Pegasus does around that DAG is genuinely best-in-class and hard to replicate:
**automatic data management** — the planner *inserts* stage-in, inter-site transfer, output
registration, and cleanup jobs into your graph, and verifies SHA-256 integrity at three
points — plus **workflow reduction** (skip jobs whose outputs already exist), **clustering**
(including ``pegasus-mpi-cluster``, an MPI master/worker that bundles a sub-DAG into one job
— a direct conceptual cousin of *HyperShell*'s task bundling), rescue-DAG fault tolerance,
and deep provenance in the **STAMPEDE** database (via ``pegasus-monitord``). It has run
flagship science, including the LIGO first detection of gravitational waves, at up to ~1
million tasks and tens of terabytes.

All of that is the payoff for real up-front declarative weight: an HTCondor pool, a Java
planning step, three catalogs, and a typed job/file graph. For a data-heavy DAG spanning
sites, it is worth every bit. For a flat bag of independent commands, it is machinery the
problem does not have. *HyperShell* inverts the bargain entirely: **no DAG, no catalogs, no
planner, no HTCondor** — flat shell-command strings from stdin/files, a self-contained server
pulling from SQLite/PostgreSQL, elastic clients, and a live SQL-queryable catalog. Both
persist per-task exit/timing to a database (Pegasus's STAMPEDE; *HyperShell*'s task catalog),
but Pegasus adds data lineage, checksums, and cross-site movement that *HyperShell* leaves to
the filesystem. Choose Pegasus when the problem **is** the dependency graph and the data
logistics; choose *HyperShell* when the problem is throughput over a huge list of independent
commands and you want the lightest possible path from commands to results.

-------------------

Where HyperShell Fits
---------------------

|

Read across the whole landscape and *HyperShell*'s niche comes into sharp focus. It is the
tool for **flat, high-throughput, language-agnostic shell work** that still wants the things
"serious" systems provide — a durable queryable record, automatic retries, elastic scale, and
even coarse ordered-phase dependencies via task groups — without a DSL, a Python API, a full
DAG engine, a broker, or a standing daemon pool. It is
GNU Parallel's interface with a database and an elastic server behind it; it is a bundler
that outlives the allocation; it is HTCondor's regime with a fraction of the operations; it is
the flat step you drop *inside* Snakemake, Nextflow, or a Slurm/Flux allocation.

The scaling mechanism worth naming explicitly is **aggregation**: the server hands work to
clients in large bundles and batches their result updates into database transactions, so
throughput is not gated by per-task dispatch or per-task scheduler submissions. This is a big
part of why server-plus-elastic-worker designs (*HyperShell*, HyperQueue) reach enormous task
counts where one-scheduler-job-per-task approaches (Nextflow's default executor, or submitting
straight to Slurm) hit contention — it is one of the most important axes on which to compare
the scaling behavior of these tools.

.. list-table:: At a glance — where each tool sits (HyperShell in the first row)
   :header-rows: 1
   :widths: 15 21 15 17 16 16

   * - Tool
     - Interface (unit of work)
     - Dependencies
     - Persistent state
     - Standing infrastructure
     - Elasticity
   * - **HyperShell**
     - Shell commands (stdin/files, ``{}`` templating, tags)
     - Flat + ordered task-group phases (no DAG)
     - Embedded SQL (SQLite/Postgres), SQL-queryable
     - Self-contained server + DB (no broker/service)
     - Elastic clients, scale-to-zero; SSH or any ``--launcher``
   * - GNU Parallel / ``xargs``
     - Shell commands (stdin/args)
     - Flat
     - Optional ``joblog`` file
     - None (single script)
     - Controller push over SSH to a (re-readable) host list
   * - HPC bundlers
     - Shell command file
     - Flat (disBatch: barriers)
     - Run-local side files
     - None (one allocation)
     - Fixed to the allocation's nodes
   * - HyperQueue
     - CLI/TOML + Python API
     - Flat (CLI) / DAG (Python)
     - In-memory + optional journal
     - Single static binary
     - Elastic; auto-submits Slurm/PBS allocations
   * - Balsam
     - Python ApplicationDefinition
     - DAG
     - Central PostgreSQL (REST)
     - Postgres + Redis + web service
     - Auto-sizes batch queue; multi-site
   * - FireWorks
     - Python Firetasks
     - DAG (+ dynamic)
     - MongoDB LaunchPad
     - MongoDB
     - Pull-based rockets; external autoscale
   * - Merlin
     - YAML study spec
     - DAG
     - Broker + results backend
     - RabbitMQ/Redis broker
     - Celery workers on allocations
   * - Dask
     - Python API (delayed/futures)
     - Dynamic DAG
     - In-memory (ephemeral)
     - Scheduler + workers
     - Adaptive; ``dask-jobqueue``
   * - Ray
     - Python API (tasks/actors)
     - Dynamic DAG (dataflow)
     - In-memory object store + GCS
     - Head node + raylets
     - Autoscaler; multi-cloud/K8s
   * - Parsl / Globus Compute
     - Python ``@apps``
     - Implicit DAG
     - In-process (+ optional SQLite monitor)
     - Driver + executor pilots
     - Blocks scale to/from zero via providers
   * - Snakemake
     - Rule DSL (Snakefile)
     - File-driven DAG
     - Filesystem + ``.snakemake``
     - Single driver (submits jobs)
     - Submit-and-monitor via executors
   * - Nextflow
     - Groovy DSL2 (processes/channels)
     - Dataflow DAG
     - Filesystem ``work/`` + LevelDB
     - JVM orchestrator
     - One scheduler job per task (pluggable executor)
   * - RADICAL-Pilot / EnTK
     - Python API (Task/Pipeline)
     - Bag (RP) / staged (EnTK)
     - Filesystem sandboxes (+ ZMQ)
     - Client + remote agent
     - Pilot holds one allocation
   * - Makeflow / TaskVine
     - Make/JX DAG; Python/C manager
     - File-level DAG
     - Log + in-cluster data cache
     - Manager + workers
     - Elastic worker pools; data-locality
   * - HTCondor
     - Submit files / ClassAds
     - Flat + DAGMan
     - ``schedd`` job queue + logs
     - Daemon pool (collector/negotiator/schedd/startd)
     - Matchmaking; glidein overlays
   * - OSPool / OSG
     - HTCondor submit files
     - Flat (+ DAGMan)
     - HTCondor queue + GRACC
     - National pool (you don't run it)
     - Opportunistic glidein overlay
   * - Pegasus
     - Python API + 3 catalogs → YAML DAG
     - DAG (data-flow)
     - STAMPEDE DB (via ``monitord``)
     - Planner + HTCondor/DAGMan
     - Via HTCondor glideins

A note on what *HyperShell* deliberately leaves out, stated plainly so the comparison is
honest. It has **no full DAG engine** — though task groups give it coarse, ordered-phase
dependencies that handle most staged pipelines; for arbitrary graphs, bring the dependencies
to Snakemake, Nextflow, or Pegasus and let *HyperShell* run the flat steps. It does **not
manage data locality** — that is TaskVine's
and Pegasus's domain; *HyperShell* relies on the shared/remote filesystem. Its running server
is a single coordination point — but task state lives in the database and is recovered across
a restart, evicted clients have their in-flight tasks reverted and rerun elsewhere, and the
autoscaler relaunches clients automatically, so the failure modes are bounded and
self-healing rather than fatal. And while the manager's RPC *framing* uses Python ``pickle``
(a consequence of building on the standard-library multiprocessing managers; the task and
heartbeat *payloads* themselves are JSON), the queue is **encrypted and authenticated by
default** — a self-signed certificate is generated on first
start and a fresh authentication key is minted per cluster invocation, so the common case is
secure with no operator action (disable only with ``--no-tls``, which we do not recommend).
The full posture, including the deliberate choices and their limits, is documented on the
:ref:`security <security>` page.

-------------------

Adjacent Worlds
---------------

|

A few neighboring ecosystems are out of scope for this page but worth naming, because they
sometimes get grouped with the above and occupy genuinely different regimes:

* **General-purpose DAG schedulers** — `Apache Airflow <https://airflow.apache.org/>`_,
  `Prefect <https://www.prefect.io/>`_, and `Luigi <https://github.com/spotify/luigi>`_ —
  are built for data-engineering pipelines (scheduled, event-driven, service-oriented), not
  HPC many-task throughput.
* **Kubernetes-native** batch — `Argo Workflows <https://argoproj.github.io/workflows/>`_,
  Kubernetes (Indexed) ``Jobs``, and `Volcano <https://volcano.sh/>`_ — target container
  orchestration on k8s rather than shell commands on an allocation.
* **Portable-workflow standards** — `CWL <https://www.commonwl.org/>`_ and
  `WDL <https://openwdl.org/>`_, with engines such as
  `Cromwell <https://cromwell.readthedocs.io/>`_ and `Toil <https://toil.readthedocs.io/>`_ —
  standardize *portable DAG definitions*, again a dependency-first regime.
* **Task-based HPC runtimes** — e.g. `PyCOMPSs/COMPSs <https://compss.bsc.es/>`_ — express
  parallelism through a programming model, closer to Dask/Ray than to a shell-command stream.

If your work is a genuine dependency graph, a containerized pipeline, or a stateful
distributed application, one of these (or one of the workflow managers above) is likely the
better fit — and *HyperShell* is happy to run underneath it as the high-throughput,
flat-fan-out step. If your work is a large list of independent shell commands and you want a
durable, queryable, elastic way to run it with almost no ceremony, that is exactly what
*HyperShell* is for.
