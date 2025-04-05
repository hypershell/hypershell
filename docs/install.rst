.. _install:

Installation
============

|

Installing *HyperShell* can take several forms. At the end of the day it is a Python package
and needs to live within some prefix and be tied to some Python runtime. As a system utility
we probably do not want to expose our dependencies to other user environments incidentally.
For these reasons, it is recommended to isolate *HyperShell* within its own virtual environment
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

        uv tool install hypershell


For `macOS` users we can accomplish the same thing with `Homebrew <https://brew.sh>`_.
This formula essentially does the same thing but managed by ``brew`` instead.


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

        mkdir -p /apps/x86_64-any/hypershell/$VERSION
        cd /apps/x86_64-any/hypershell/$VERSION

        git clone --depth=1 --branch=$VERSION https://github.com/hypershell/hypershell src

        python3.12 -m venv libexec
        libexec/bin/pip install ./src
        libexec/bin/pip install psycopg2

        mkdir -p bin
        ln -sf ../libexec/bin/hs bin/hs
        ln -sf src/share

|

Based on this installation, a simple `LMOD <https://lmod.readthedocs.io/en/latest/>`_
configuration file might then be:

.. admonition:: Module file definition (e.g., /etc/module/x86_64-any/hypershell/<VERSION>.lua)
    :class: note

    .. code-block:: lua

        local appname = "hypershell"
        local version = "<version>" -- replace with actual version
        local appsdir = "/apps/x86_64-any"
        local modroot = pathJoin(appsdir, appname, version)

        whatis("Name: HyperShell")
        whatis("Version: " .. version)
        whatis("Description: A cross-platform, high-throughput computing utility for processing
        shell commands over a distributed, asynchronous queue.")

        prepend_path("PATH", pathJoin(modroot, "bin"))
        prepend_path("MANPATH", pathJoin(modroot, "share", "man"))

        -- Raw source b/c `complete -F _hs hs` does not persist with source_sh
        execute { cmd="source " .. pathJoin(modroot, "share", "bash_completion.d", "hs"), modeA={"load"} }

Presumably, users would then be able to activate the software by loading the module as such:

.. admonition:: Load module
    :class: note

    .. code-block:: shell

        module load hypershell

------

Runtime Package Resolution
--------------------------

|

.. include:: _include/config_pythonpath.rst

For example, from the above installation we might add the following to our module:

.. admonition:: Extra setting for LMOD
    :class: note

    .. code-block:: lua

        ...
        prepend_path("HYPERSHELL_PYTHONPATH", pathJoin(modroot, "frozen-python.path"))
        ...

And we can include the following paths in our frozen set.

.. admonition:: Contents of ``frozen-python.path``
    :class: note

    .. code-block:: shell

        /apps/x86_64-any/hypershell/<version>/libexec/lib/python3.12
        /apps/x86_64-any/hypershell/<version>/libexec/lib/python3.12/lib-dynload
        /apps/x86_64-any/hypershell/<version>/libexec/lib/python3.12/site-packages


-------------------

Development
-----------

|

As a library dependency, `HyperShell` can easily be added to your project using whatever package
tooling you like. For development of `HyperShell` itself, contributors should create their own fork
of the repository on `GitHub <https://github.com/hypershell/hypershell>`_ and clone the fork locally.
We use `uv <https://docs.astral.sh/uv/>`_ for managing the development environment. The
``uv.lock`` file is included in the repository, simply run the following command to initialize
your virtual environment.

.. admonition:: Install development dependencies inside local forked repository
    :class: note

    .. code-block:: shell

        uv sync

|
