Manual Page
===========

Synopsis
--------

hs [-h] [-v] [--citation] ...
    Top-level command. Show help, version, or citation info.

hs cluster [-h] *FILE* [--ssh *HOST*... | --mpi | --launcher *ARGS*...] ...
    Run managed cluster.

hs submit [-h] *FILE* ...
    Submit tasks from file.

hs server [-h] *FILE* ...
    Run stand-alone server.

hs client [-h] ...
    Run stand-alone client.

hs config [-h] {get | set | which | edit } ...
    Configuration management.

hs task [-h] {submit | info | wait | run | search | update} ...
    Task management.

hs initdb [-h] [--truncate [--yes]]
    Initialize database.

Description
-----------

HyperShell is an elegant, cross-platform, high-throughput computing utility for processing
shell commands over a distributed, asynchronous queue. It is a highly scalable workflow automation
tool for many-task scenarios.

Typically, ad hoc usage or batch jobs will use the ``cluster`` workflow. This automatically stands
up the ``server`` and one or more ``client`` instances on remote servers and processes the commands
from some input *FILE* until completion.

Operate with an in-memory queue, or configure a database to manage task scheduling and persistence.
Stand up the ``server`` on its own and scale ``clients`` as desired, and ``submit`` tasks independently.


Cluster Usage
-------------

.. include:: _include/cluster_usage.rst

.. include:: _include/cluster_desc.rst

.. include:: _include/cluster_help.rst


Server Usage
------------

.. include:: _include/server_usage.rst

.. include:: _include/server_desc.rst

.. include:: _include/server_help.rst


Client Usage
------------

.. include:: _include/client_usage.rst

.. include:: _include/client_desc.rst

.. include:: _include/client_help.rst


Submit Usage
------------

.. include:: _include/submit_usage.rst

.. include:: _include/submit_desc.rst

.. include:: _include/submit_help.rst


Initdb Usage
------------

.. include:: _include/initdb_usage.rst

.. include:: _include/initdb_desc.rst

.. include:: _include/initdb_help.rst


Config Get Usage
----------------

.. include:: _include/config_get_usage.rst

.. include:: _include/config_get_desc.rst

.. include:: _include/config_get_help.rst


Config Set Usage
----------------

.. include:: _include/config_set_usage.rst

.. include:: _include/config_set_desc.rst

.. include:: _include/config_set_help.rst


Config Edit Usage
-----------------

.. include:: _include/config_edit_usage.rst

.. include:: _include/config_edit_desc.rst

.. include:: _include/config_edit_help.rst


Config Which Usage
------------------

.. include:: _include/config_which_usage.rst

.. include:: _include/config_which_desc.rst

.. include:: _include/config_which_help.rst


Task Submit Usage
-----------------

.. include:: _include/task_submit_usage.rst

.. include:: _include/task_submit_desc.rst

.. include:: _include/task_submit_help.rst


Task Info Usage
---------------

.. include:: _include/task_info_usage.rst

.. include:: _include/task_info_desc.rst

.. include:: _include/task_info_help.rst


Task Wait Usage
---------------

.. include:: _include/task_wait_usage.rst

.. include:: _include/task_wait_desc.rst

.. include:: _include/task_wait_help.rst


Task Run Usage
--------------

.. include:: _include/task_run_usage.rst

.. include:: _include/task_run_desc.rst

.. include:: _include/task_run_help.rst


Task Search Usage
-----------------

.. include:: _include/task_search_usage.rst

.. include:: _include/task_search_desc.rst

.. include:: _include/task_search_help.rst


Task Update Usage
-----------------

.. include:: _include/task_update_usage.rst

.. include:: _include/task_update_desc.rst

.. include:: _include/task_update_help.rst


Templates
---------

.. include:: _include/templates_alt.rst


Configuration
-------------

.. include:: _include/config_intro_alt.rst


Parameter Reference
^^^^^^^^^^^^^^^^^^^

.. include:: _include/config_param_ref.rst


Database
--------

.. include:: _include/database_alt.rst


Environment Variables
---------------------

As stated for configuration, any environment variable prefixed as ``HYPERSHELL_``
where the name aligns to the path to some option, delimited by underscores,
will set that option. Example, ``HYPERSHELL_CLIENT_TIMEOUT`` maps to the
corresponding configuration option.

The following environment variables must be specified as such and cannot be configurable
within files.

.. include:: _include/config_site_vars.rst

.. include:: _include/config_pythonpath.rst

.. include:: _include/config_task_env_alt.rst

We also respect setting the following environment variables to force disable/enable
the use of colors in all console output.

``NO_COLOR``
    If this variable is set to anything but a blank string, all colors are disabled.
    See `no-color.org <https://no-color.org>`_ for details.

``FORCE_COLOR``
    If this variable is set to anything but a blank string, colors will be enabled
    regardless of whether `stdout` or `stderr` are a TTY.

Signals
-------

HyperShell traps the following UNIX signals (does not apply on Microsoft Windows).

.. include:: _include/signals.rst


Exit Status
-----------

.. include:: _include/exit_status.rst


See Also
--------

ssh(1), mpirun(1)
