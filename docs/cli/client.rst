.. _cli_client:

Client
======

Usage
-----

.. include:: ../_include/client_usage.rst

Description
-----------

.. include:: ../_include/client_desc.rst

|

.. versionadded:: 2.9.0
   ``--zone`` (:pr:`78`).

.. versionchanged:: 2.9.0
   :pr:`78`: the client requests each bundle from the server, and ``-b``/``--bundlesize`` is the
   size it asks for; left unset, the server's size applies.

.. versionchanged:: 2.9.0
   A key on the command line is refused; the client takes it from the environment or a configuration
   file.

.. include:: ../_include/client_help.rst
