.. _cli_initdb:

Initdb
======

Usage
-----

.. include:: ../_include/initdb_usage.rst

Description
-----------

.. include:: ../_include/initdb_desc.rst

|

.. versionchanged:: 2.9.0
   :pr:`69`: an existing database from an earlier release is upgraded in place on first use.
   :pr:`59`: ``--rotate`` records the partition in each moved task's ``part`` column, not a
   ``part:N`` tag.

.. include:: ../_include/initdb_help.rst
