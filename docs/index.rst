What is HyperShell?
===================

Release v\ |release| (:ref:`Getting Started <getting_started>`)

.. image:: https://img.shields.io/badge/license-Apache-blue.svg?style=flat
    :target: https://www.apache.org/licenses/LICENSE-2.0
    :alt: License

.. image:: https://img.shields.io/github/v/release/hypershell/hypershell?sort=semver
    :target: https://github.com/hypershell/hypershell/releases
    :alt: Github Release

.. image:: https://img.shields.io/badge/Python-3.9+-blue.svg
    :target: https://www.python.org/downloads
    :alt: Python Versions

.. image:: https://img.shields.io/badge/Contributor%20Covenant-2.1-4baaaa.svg
    :target: https://www.contributor-covenant.org/version/2/1/code_of_conduct/
    :alt: Code of Conduct

|

.. include:: _include/desc.rst

Built on Python and tested on Linux, macOS, and Windows.

Several tools offer similar functionality but not all together in a single tool with
the user ergonomics we provide. Novel design elements include but are not limited to:

* **Cross-platform:** run on any platform where Python runs. In fact, the server and
  client can run on different platforms in the same cluster.
* **Client-server:** workloads do not need to be monolithic. Run the server as a
  stand-alone service with SQLite or Postgres as a persistent database and dynamically
  scale clients as needed.
* **Staggered launch:** At the largest scales (1000s of nodes, 100k+ of workers),
  the launch process can be challenging. Come up gradually to balance the workload.
* **Database in-the-loop:** run in-memory for quick, ad-hoc workloads. Otherwise,
  include a database for persistence, recovery when restarting, and search.

-------------------

Usage
-----

|

*HyperShell* is primarily a :ref:`command-line <cli>` program.
Most users will operate the ``hs cluster`` in a start-to-finish workflow scenario much
like people tend to do with alternatives like ``xargs``, `GNU Parallel <https://gnu.org/software/parallel>`_,
or HPC-specific tools like `ParaFly <https://parafly.sourceforge.net>`_ or
`TaskFarmer <https://docs.nersc.gov/jobs/workflow/taskfarmer/>`_ (NERSC-only) or
`Launcher <https://tacc.utexas.edu/research/tacc-research/launcher/>`_ (TACC).

.. admonition:: Basic usage
    :class: note

    .. code-block:: shell

        seq 1000000 | hs cluster -t 'echo {}' -N64 --ssh 'a[00-32].cluster' > task.out


See :ref:`getting started <getting_started>` for features and additional usage examples.
Specific documentation is available for :ref:`configuration <config>` management,
:ref:`database <database>` setup, :ref:`logging <logging>`, and using :ref:`templates <templates>`.

The *HyperShell* :ref:`server <cli_server>` can operate in standalone mode along side the database.
Zero or more :ref:`client <cli_client>` instances may come and go as available and process tasks.
When deployed in this fashion, the cluster can scale out as necessary as well as scale down to zero.
This strategy is appropriate for creating shared, autoscaling, high-throughput pipelines for
facilities with multiple users.

*HyperShell* also provides a :ref:`library <library>` interface for Python applications to embed components.
Developers can add *HyperShell* to their project to provide all of this functionality within their own
applications or Python-based workflows.

-------------------

Support
-------

|

Join the `Discord <https://discord.gg/wmv5gyUfkN>`_ server to post questions, discuss your project,
share with the community, keep in touch with announcements and upcoming events!

*HyperShell* is an open-source project developed on `GitHub <https://github.com/hypershell/hypershell>`_.
If you find bugs or issues with the software please create an `Issue <https://github.com/hypershell/hypershell/issues>`_.
Contributions are welcome in the form of `Pull requests <https://github.com/hypershell/hypershell/pulls>`_
for bug fixes, documentation, and minor feature improvements.

-------------------

License
-------

|

*HyperShell* is released under the
`Apache Software License (v2) <https://www.apache.org/licenses/LICENSE-2.0>`_.

    .. include:: _include/license.rst

-------------------

Citation
--------

|

If this software has helped facilitate your research please consider citing us.

.. include:: _include/citation.rst


|

.. toctree::
    :hidden:
    :caption: Intro

    getting_started
    install

.. toctree::
    :hidden:
    :caption: Reference

    cli/index
    api/index
    config
    logging
    database
    templates

.. toctree::
    :hidden:
    :caption: Tutorial

    tutorial/basic
    tutorial/distributed
    tutorial/hybrid
    tutorial/advanced

.. toctree::
    :hidden:
    :caption: Project

    blog/index
    roadmap
