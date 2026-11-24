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
   :pr:`117`: the value is written by the key's type: text stays text, and a whole number, a number
   or a boolean is converted or refused (exit status 2) before anything is written.

.. versionchanged:: 2.9.0
   A numeric key's value is held to the key's range: a value outside it is refused (exit status 2)
   and nothing is written, where before a duration or a size was written as given and checked by
   the command that read it. An integer for a key with no type of its own is refused outside 64 bits.
   A value past the key's advisory limit is written, with a warning.

.. include:: ../_include/config_set_help.rst
