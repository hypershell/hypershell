HyperShell v2: Distributed Task Execution for HPC
=================================================

.. image:: https://img.shields.io/badge/license-Apache-blue.svg?style=flat
    :target: https://www.apache.org/licenses/LICENSE-2.0
    :alt: License

.. image:: https://img.shields.io/github/v/release/hypershell/hypershell?sort=semver
    :target: https://github.com/hypershell/hypershell/releases
    :alt: Github Release

.. image:: https://img.shields.io/badge/Python-3.9+-blue.svg
    :target: https://www.python.org/downloads
    :alt: Python Versions

.. image:: https://static.pepy.tech/badge/hypershell/month
    :target: https://pypi.org/project/hypershell/
    :alt: PyPI Monthly Downloads

.. image:: https://img.shields.io/badge/Contributor%20Covenant-2.1-4baaaa.svg
    :target: https://www.contributor-covenant.org/version/2/1/code_of_conduct/
    :alt: Code of Conduct

.. image:: https://readthedocs.org/projects/hypershell/badge/?version=latest
    :target: https://hypershell.readthedocs.io/en/latest/?badge=latest
    :alt: Documentation Status

.. image:: https://github.com/hypershell/hypershell/actions/workflows/tests.yml/badge.svg
    :target: https://github.com/hypershell/hypershell/actions/workflows/tests.yml
    :alt: Tests

|

.. |br| raw:: html

   <br />

*HyperShell* is an elegant, cross-platform, high-throughput computing utility for
processing shell commands over a distributed, asynchronous queue. It is a highly
scalable workflow automation tool for *many-task* scenarios.

Built on Python and tested on Linux, macOS, and Windows.

Other tools may offer similar functionality in some places but not within a single tool and
not with the flexibility, ergonomics, and scalability provided by HyperShell.

Design elements include but are not limited to:

* **Client-server:** Run the server in stand-alone mode with SQLite or PostgreSQL. |br|
  Scale clients elastically as needed (even down to zero).
* **Cross-platform:** trivial to install, run on any platform where Python runs. |br|
  Mix platforms within a running cluster (Server on Linux, Clients on Windows).
* **Staggered launch:** Come up gradually to balance the workload. |br|
  Scale to 1000+ nodes, 250k+ workers without crashing the server.
* **Database in-the-loop:** persist task metadata across runs. |br|
  Fault-tolerant by default. Automated retries. Task history.
* **User-defined tags:** annotate tasks with `key`:`value` tags. |br|
  Manage catalogs of large collections of tasks with ease.


Usage
-----

*HyperShell* is primarily a command-line program.
Most users will operate the ``hs cluster`` command (``hsx`` for short) in a start-to-finish workflow scenario much
like people tend to do with alternatives like ``xargs``, `GNU Parallel <https://gnu.org/software/parallel>`_,
or HPC-specific tools like `ParaFly <https://parafly.sourceforge.net>`_ or
`TaskFarmer <https://docs.nersc.gov/jobs/workflow/taskfarmer/>`_ (NERSC-only) or
`Launcher <https://tacc.utexas.edu/research/tacc-research/launcher/>`_ (TACC).

.. code-block:: shell

    seq 1000000 | hsx -t 'echo {}' -N64 --ssh 'a[00-32].cluster' > task.out


Documentation
-------------

Documentation is available at `hypershell.readthedocs.io <https://hypershell.readthedocs.io>`_.
For basic usage information on the command line use: ``hs --help``. For a more
comprehensive usage guide on the command line you can view the manual page with 
``man hs``.


Support and Contributions
-------------------------

Join the `Discord <https://discord.gg/wmv5gyUfkN>`_ server to post questions, discuss your project,
share with the community, keep in touch with announcements and upcoming events!

*HyperShell* is an open-source project developed on `GitHub <https://github.com/hypershell/hypershell>`_.
If you find bugs or issues with the software please create an `Issue <https://github.com/hypershell/hypershell/issues>`_.
Contributions are welcome in the form of `Pull requests <https://github.com/hypershell/hypershell/pulls>`_
for bug fixes, documentation, and minor feature improvements.

We've added a Code of Conduct recently, adapted from the
`Contributor Covenant <https://www.contributor-covenant.org/>`_, version 2.0.


License
-------

*HyperShell* is released under the
`Apache Software License (v2) <https://www.apache.org/licenses/LICENSE-2.0>`_.


Citation
--------

If *HyperShell* has helped in your research please consider citing us.

.. code-block:: bibtex

    @inproceedings{lentner_2022,
        author = {Lentner, Geoffrey and Gorenstein, Lev},
        title = {HyperShell v2: Distributed Task Execution for HPC},
        year = {2022},
        isbn = {9781450391610},
        publisher = {Association for Computing Machinery},
        url = {https://doi.org/10.1145/3491418.3535138},
        doi = {10.1145/3491418.3535138},
        booktitle = {Practice and Experience in Advanced Research Computing},
        articleno = {80},
        numpages = {3},
        series = {PEARC '22}
    }
