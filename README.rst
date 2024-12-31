HyperShell v2: Distributed Task Execution for HPC
=================================================

.. image:: https://img.shields.io/badge/license-Apache-blue.svg?style=flat
    :target: https://www.apache.org/licenses/LICENSE-2.0
    :alt: License

.. image:: https://img.shields.io/github/v/release/hypershell/hypershell?sort=semver
    :target: https://github.com/hypershell/hypershell/releases
    :alt: Github Release

.. image:: https://img.shields.io/badge/Python-3.10+-blue.svg
    :target: https://www.python.org/downloads
    :alt: Python Versions

.. image:: https://img.shields.io/badge/Contributor%20Covenant-2.1-4baaaa.svg
    :target: https://www.contributor-covenant.org/version/2/1/code_of_conduct/
    :alt: Code of Conduct

.. image:: https://readthedocs.org/projects/hypershell/badge/?version=latest
    :target: https://hypershell.readthedocs.io/en/latest/?badge=latest
    :alt: Documentation Status

|

*HyperShell* is an elegant, cross-platform, high-throughput computing utility for
processing shell commands over a distributed, asynchronous queue. It is a highly
scalable workflow automation tool for *many-task* scenarios.

Built on Python and tested on Linux, macOS, and Windows.

Several tools offer similar functionality but not all together in a single tool with
the user ergonomics we provide. Novel design elements include but are not limited to

* **Cross-platform:** run on any platform where Python runs. In fact, the server and
  client can run on different platforms in the same cluster.
* **Client-server:** workloads do not need to be monolithic. Run the server as a
  stand-alone service with SQLite or Postgres as a persistent database and dynamically
  scale clients as needed.
* **Staggered launch:** At the largest scales (1000s of nodes, 100k+ of workers),
  the launch process can be challenging. Come up gradually to balance the workload.
* **Database in-the-loop:** run in-memory for quick, ad-hoc workloads. Otherwise,
  include a database for persistence, recovery when restarting, and search.


Documentation
-------------

Documentation is available at `hypershell.readthedocs.io <https://hypershell.readthedocs.io>`_.
For basic usage information on the command line use: ``hs --help``. For a more
comprehensive usage guide on the command line you can view the manual page with 
``man hs``.


Contributions
-------------

Contributions are welcome. If you find bugs or have questions, open an *Issue* here.
We've added a Code of Conduct recently, adapted from the
`Contributor Covenant <https://www.contributor-covenant.org/>`_, version 2.0.


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
