.. _install:

Installation
============

|

Installing *HyperShell* can take several forms. At the end of the day it is a Python package
and needs to live within some prefix and be tied to some Python runtime. As a system utility
we probably do not want to expose our dependencies to other user environments incidentally.
For these reason, it is recommended to isolate *HyperShell* within its own virtual environment
and only exposed the top-level entry point *script* to the users `PATH`.

-------------------

Basic Installation
------------------

|

The `uv <https://docs.astral.sh/uv/>`_ utility wraps all of this up nicely for user-level
installations. On any platform, if installing for yourself, especially if you lack root
or administrative privileges, we recommend the following.

.. admonition:: Install HyperShell using uv
    :class: note

    .. code-block:: shell

        uv tool install git+https://github.com/hypershell/hypershell

.. warning::

        The `HyperShell` project has transitioned away from using the hyphen in any
        context (command-line, filesystem, variables, online documentation, etc).
        But because of a temporary naming `issue <https://github.com/pypi/support/issues/4104>`_
        with the Python Package Index (pypi.org, pip) we have not yet secured the unhyphenated
        ``hypershell`` name on the index. So until then, we recommend installing from GitHub.


For `macOS` users we can accomplish the same thing with `Homebrew <https://brew.sh>`_.
This formula essentially does the same thing as Pipx but managed by ``brew`` instead.


.. admonition:: Install HyperShell using Homebrew
    :class: note

    .. code-block:: shell

        brew tap hypershell/tap
        brew install hypershell

-------------------

Advanced Installation
---------------------

|

System administrators may want to install and expose `HyperShell` in a custom location.
On something like an HPC cluster this could be an entirely different file system.
Let us assume this is the case, and that we already have our own Python installation
managed by some `module` system.

Here we will create an isolated prefix for the installation with version number included
and only expose the entry-point scripts to users, along with shell completions and the
manual page. Some desired runtime, ``python3.12``, is already loaded.

.. admonition:: Create installation manually on a shared system
    :class: note

    .. code-block:: shell

        mkdir -p /apps/x86_64-any/hypershell/2.6.3
        cd /apps/x86_64-any/hypershell/2.6.3

        mkdir -p bin share
        git clone --depth 1 --branch 2.6.3 https://github.com/hypershell/hypershell ./src

        python3.12 -m venv libexec
        libexec/bin/pip install psycopg2
        libexec/bin/pip install ./src

        ln -sf ../libexec/bin/hs bin/hs
        ln -sf ../src/man share/man
        ln -sf src/src/completions

|

Based on this installation, a simple `LMOD <https://lmod.readthedocs.io/en/latest/>`_
configuration file might then be:

.. admonition:: Module file definition (e.g., /etc/module/x86_64-any/hypershell/2.6.3.lua)
    :class: note

    .. code-block:: lua

        local appname = "hypershell"
        local version = "2.6.3"
        local appsdir = "/apps/x86_64-any"
        local modroot = pathJoin(appsdir, appname, version)

        whatis("Name: HyperShell")
        whatis("Version: " .. version)
        whatis("Description: A cross-platform, high-throughput computing utility for processing
        shell commands over a distributed, asynchronous queue.")

        prepend_path("PATH", pathJoin(modroot, "bin"))
        prepend_path("MANPATH", pathJoin(modroot, "share", "man"))

        -- Raw source b/c `complete -F _hs hs` does not persist with source_sh
        execute { cmd="source " .. pathJoin(modroot, "completions", "hypershell.sh"), modeA={"load"} }

Presumably, users would then be able to activate the software by loading the module as such:


.. admonition:: Load module
    :class: note

    .. code-block:: shell

        module load hypershell

-------------------

Development
-----------

|

As a library dependency, `HyperShell` can easily be added to your project using whatever package
tooling you like. For development of `HyperShell` itself, contributors should create their own fork
of the repository on `GitHub <https://github.com/hypershell/hypershell>`_ and clone the fork locally.
We use `Poetry <https://python-poetry.org>`_ for managing the development environment. The
``poetry.lock`` file is included in the repository, simply run the following command to initialize
your virtual environment.

.. admonition:: Install development dependencies inside local forked repository
    :class: note

    .. code-block:: shell

        poetry install

|
