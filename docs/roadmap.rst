.. _roadmap:

Roadmap
=======

The current release of `HyperShell` is nearly feature complete. Inevitably there will be additional
bug fixes, improvements, and refactorings. Below is a list of project items we're considering for
the near future.

-------------------

Tutorials and Walkthroughs
--------------------------

**End of 2026**

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

Website
-------

**End of 2026**

We have the `hypershell.org <https://hypershell.org>`_ domain and are working on a beautiful front-end
website to act as a landing page for the project with additional content and information.

-------------------

Affiliate Packages
------------------

**End of 2026**

`HyperShell` provides high-throughput scheduling on HPC clusters where policies and practical
considerations prevent direct scheduling of small tasks (e.g., with Slurm). For all the reasons
one might need this kind of program, so too would a workflow system like
`NextFlow <https://www.nextflow.io>`_. We are working on a plugin to allow use of `HyperShell`
as an execution backend for `NextFlow` pipelines, ``hypershell-nextflow``.
