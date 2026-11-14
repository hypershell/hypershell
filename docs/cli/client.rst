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

.. versionchanged:: 2.9.0
   ``-w``/``--bundlewait``, ``-S``/``--signalwait`` and ``-d``/``--delay-start`` take a duration
   with units or a Slurm time, as ``-W`` does, and a signed value is refused except on
   ``--delay-start``, where a leading ``-`` still asks for a random delay (e.g.,
   ``--delay-start=-10m``).

.. include:: ../_include/client_help.rst
