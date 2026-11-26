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
   :pr:`104`: a key on the command line is refused; the client takes it from the environment or a
   configuration file.

.. versionchanged:: 2.9.0
   :pr:`117`: ``-R``/``--ratelimit`` takes a number (e.g., ``-R 0.5``, one task every two seconds),
   as ``client.ratelimit`` does. A rate other than ``0`` below the range is refused.

.. versionchanged:: 2.9.0
   :pr:`106`: ``-w``/``--bundlewait``, ``-S``/``--signalwait`` and ``-d``/``--delay-start`` take a
   duration with units or a Slurm time, as ``-W`` does, and a signed value is refused except on
   ``--delay-start``, where a leading ``-`` still asks for a random delay (e.g.,
   ``--delay-start=-10m``).

.. versionchanged:: 2.9.0
   :pr:`115`: a client with nothing left to run (no task running and none waiting to start) returns
   the results it holds at once, instead of waiting out ``-w``/``--bundlewait``. With work in hand
   it bundles as before.

.. versionchanged:: 2.9.0
   :pr:`119`: ``hs client`` reads ``client.bundlesize`` (or ``HYPERSHELL_CLIENT_BUNDLESIZE``) and
   ``client.bundlewait`` for its ``-b`` and ``-w`` defaults; ``submit.bundlesize`` and
   ``submit.bundlewait`` no longer reach it (move them to ``[client]``).

.. versionchanged:: 2.9.0
   :pr:`122`: with no ``-H``/``--host``, the client dials ``server.host``, else
   ``server.advertise``, else a specific ``server.bind``, else *localhost*; before, it dialled
   ``server.bind``.

.. versionchanged:: 2.9.0
   :pr:`125`: each numeric option has a range. A value outside it, or
   one that is not a number of the option's kind, stops the command before it starts, with one line
   naming the option, the range and the value (exit status 2). An option with an advisory limit
   takes a value past it and runs, with one warning. A negative ``-R``/``--ratelimit`` is refused.

.. include:: ../_include/client_help.rst
