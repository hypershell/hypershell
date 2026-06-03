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

.. image:: https://static.pepy.tech/badge/hypershell/month
    :target: https://pypi.org/project/hypershell/
    :alt: PyPI Monthly Downloads

.. image:: https://img.shields.io/badge/Contributor%20Covenant-2.1-4baaaa.svg
    :target: https://www.contributor-covenant.org/version/2/1/code_of_conduct/
    :alt: Code of Conduct

.. image:: https://github.com/hypershell/hypershell/actions/workflows/tests.yml/badge.svg
    :target: https://github.com/hypershell/hypershell/actions/workflows/tests.yml
    :alt: Tests

|

.. include:: _include/desc.rst

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

-------------------

Usage
-----

|

*HyperShell* is primarily a :ref:`command-line <cli>` program.
Most users will operate the ``hs cluster`` command (``hsx`` for short) in a start-to-finish workflow scenario much
like people tend to do with alternatives like ``xargs``, `GNU Parallel <https://gnu.org/software/parallel>`_,
or HPC-specific tools like `ParaFly <https://parafly.sourceforge.net>`_ or
`TaskFarmer <https://docs.nersc.gov/jobs/workflow/taskfarmer/>`_ (NERSC-only) or
`Launcher <https://tacc.utexas.edu/research/tacc-research/launcher/>`_ (TACC).

.. admonition:: Basic usage
    :class: note

    .. code-block:: shell

        seq 1000000 | hsx -t 'echo {}' -N64 --ssh 'a[00-32].cluster' > task.out


See :ref:`getting started <getting_started>` for features and additional usage examples.
Specific documentation is available for :ref:`configuration <config>` management,
:ref:`database <database>` setup, :ref:`logging <logging>`, and using :ref:`templates <templates>`.

The *HyperShell* :ref:`server <cli_server>` can operate in standalone mode alongside the database.
Zero or more :ref:`client <cli_client>` instances may come and go as available and process tasks.
When deployed in this fashion, the cluster can scale out as necessary as well as scale down to zero.
This strategy is appropriate for creating shared, autoscaling, high-throughput pipelines for
facilities with multiple users.

*HyperShell* also provides a :ref:`library <library>` interface for Python applications to embed components.
Developers can add *HyperShell* to their project to provide all of this functionality within their own
applications or Python-based workflows.

-------------------

Domain Use Cases
----------------

|

*HyperShell* is designed for embarrassingly parallel workloads across many scientific and
engineering domains. Whether processing millions of files, running massive parameter sweeps,
or executing independent computational tasks, *HyperShell* provides the infrastructure to
scale and manage your workflow with confidence.

Natural Sciences
^^^^^^^^^^^^^^^^

* **Genomics** / **Proteomics**: Sequence alignment, variant calling, genome assembly
* **Bioinformatics**: Pipeline orchestration, batch analysis of biological datasets
* **Pharmacy** / **Drug Discovery**: Molecular docking, virtual screening, clinical trial simulations
* **Climate Science** / **Weather Modeling**: Ensemble forecasts, climate scenario analysis
* **Materials Science**: Molecular dynamics simulations, high-throughput property screening
* **Computational Chemistry**: Energy calculations, reaction pathway analysis
* **Geoscience** / **Seismology**: Seismic data processing, geological survey analysis
* **Neuroscience**: Brain imaging analysis, neural network simulations, connectome mapping
* **High-Energy Physics**: Collider data processing, event reconstruction
* **Astronomy** / **Physics**: Sky surveys, photon-transport simulations, particle physics analysis
* **Cosmology**: gravitational wave analysis

Engineering
^^^^^^^^^^^

* **Computational Fluid Dynamics (CFD)**: Parameter sweeps for design optimization
* **Finite Element Analysis (FEA)**: Structural analysis, stress testing, mesh refinement studies
* **Computer Vision** / **Image Processing**: Batch image analysis, object detection pipelines
* **Rendering** / **Visual Effects (VFX)**: Distributed rendering, animation frame processing
* **Network Simulation** / **Cybersecurity**: Traffic analysis, penetration testing, security audits
* **Financial Modeling** / **Risk Analysis**: Monte Carlo simulations, portfolio optimization

Computer Science & Data Science
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

* **Artificial Intelligence**: inferencing, model evaluation, model training
* **Machine Learning**: Hyperparameter tuning, feature engineering
* **Data Science**: Benchmarking, data preprocessing, analysis
* **Monte Carlo Simulations**: Statistical sampling, uncertainty quantification, stochastic modeling

Mathematics & Optimization
^^^^^^^^^^^^^^^^^^^^^^^^^^

* **Numerical Methods**: Parameter sweeps, sensitivity analysis, convergence studies
* **Optimization**: Grid search, genetic algorithms, multi-objective optimization
* **Statistical Computing**: Bootstrap resampling, permutation tests, computational inference


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

.. admonition:: BibTeX citation
    :class: note

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
