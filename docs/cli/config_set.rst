.. _cli_config_set:

Config Set
==========

Usage
-----

.. include:: ../_include/config_set_usage.rst

Description
-----------

.. include:: ../_include/config_set_desc.rst

|

.. versionchanged:: 2.9.0
   The value is written by the key's type: text stays text, and a whole number, a number or a
   boolean is converted or refused (exit status 2) before anything is written.

.. include:: ../_include/config_set_help.rst
