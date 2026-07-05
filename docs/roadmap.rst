.. _roadmap:

Roadmap
=======

The current release of `HyperShell` is nearly feature complete. Inevitably there will be additional
bug fixes, improvements, and refactorings. Below is a list of project items we're considering for
the near future.


-------------------

Tutorials and Walkthroughs
--------------------------

**End of 2024**

|

We've been working hard for the past year to put together a series of real-world scientific use-cases
with tangible data (or inputs) that users can download and run on their own to learn about all the
different ways `HyperShell` can be used with its myriad features.

Despite a few setbacks, we plan to have topics on Bioinformatics/genomics with RNA-sequence data
(likely agricultural) as a bog-standard scenario. Similarly in Astronomy with data reduction pipelines.

For the larger, extreme end of the high-throughput regime we hope to include something from Mathematics
with an optimized C++ application to validate prime numbers, run at scale.

We'll include everything in the tutorial sections here on the website. Additionally though we're putting
together an extended workshop as part of the `ACCESS <http://access-ci.org>`_ community of NSF-funded
high-performance computing *resource providers* here at Purdue University. This will be simulcast among
multiple institutions and we'll hopefully publish the recording here on the website as well.

-------------------

Testing and Benchmarks
----------------------

**End of 2024**

|

We've done extensive realworld testing and performance analysis on numerous systems including
multiple Top500 supercomputers, such as `Anvil <https://www.rcac.purdue.edu/anvil>`_ at Purdue and
`Summit <https://www.olcf.ornl.gov/olcf-resources/compute-systems/summit/>`_ at Oak Ridge National Lab.

We plan to add an automated, end-to-end test suite to the project to establish reproducible statistics
for comparison against platforms, architectures, versions of Python, etc. We already have a private
repository with scale-out tests but we hope to clean this up and abstract it to go more places.

-------------------

Website
-------

**End of 2024**

|

We have the `hypershell.org <https://hypershell.org>`_ domain and are working on a beautiful front-end
website to act as a landing page for the project with additional content and information.

-------------------

Refactoring
-----------

**Early 2025**

|

The current project is a single Python package. We have other affiliate packages in mind and would like
to keep everything under one roof as a small monorepo on GitHub. This would allow for installing
``hypershell-core`` as a dependency in Python projects without the need to pollute them with the
command-line interface. But also extra projects, like ``hypershell-nextflow`` (see next).

-------------------

Affiliate Packages
------------------

**Early 2025**

|

`HyperShell` provides high-throughput scheduling on HPC clusters where policies and practical
considerations prevent direct scheduling of small tasks (e.g., with Slurm). For all the reasons
one might need this kind of program, so too would a workflow system like
`NextFlow <https://www.nextflow.io>`_. We are working on a plugin to allow use of `HyperShell`
as an execution backend for `NextFlow` pipelines.

-------------------

Features
--------

**End of 2025**

|

`HyperShell` is essentially feature complete. But there are a few things that may just yet be
useful additions to the system. Here are a few ideas that we already have the basics for.

**Resource Monitoring**
    Automatic capture of resource utilization for both node-level (clients) and task-level.
    This would include both CPU and main memory usage of the processes as well as things like
    GPU usage (Nvidia, ROCm). This would function in the same way as capture of <stdout> and
    <stderr> for tasks. Configuration and/or command-line options would trigger client or
    task-level telemetry.

**Database Partitioning**
    For extreme scale (10M+ task) clusters using a database (likely PostgreSQL) there are
    performance issues with task scheduling and updates. Postgres (and its extension ecosystem)
    already have robust tools for helping this situation. We could build some of this into
    the program itself to make it easier for novice users.

**Plugin System**
    It might be useful to enable event-driven custom behavior when using `HyperShell` as a
    library within your project. With events like `on_submit`, `on_schedule`, or `on_returned`,
    one could register functions to affect new behavior.
