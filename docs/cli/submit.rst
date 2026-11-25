.. _cli_submit:

Submit
======

Usage
-----

.. include:: ../_include/submit_usage.rst

Description
-----------

.. include:: ../_include/submit_desc.rst

|

.. versionadded:: 2.9.0
   ``--from-json`` (:pr:`48`); ``--repeat`` and ``--update`` (:pr:`50`); ``--zone`` (:pr:`78`).

.. versionchanged:: 2.9.0
   :pr:`50`: submitting a named task file that was already submitted is refused unless
   ``--repeat`` or ``--update`` is given. :pr:`78`: ``-q``/``--queue`` needs a server started
   with ``--no-db``.

.. versionchanged:: 2.9.0
   :pr:`104`: a key on the command line is refused; ``--queue`` takes it from the environment or a
   configuration file.

.. versionchanged:: 2.9.0
   :pr:`106`: ``-w``/``--bundlewait`` takes a duration with units or a Slurm time, as ``-W`` does,
   and a signed value is refused.

.. versionchanged:: 2.9.0
   :pr:`122`: with ``--queue`` and no ``-H``, it dials ``server.advertise`` or a specific
   ``server.bind`` when ``server.host`` is not given, as `client` does; before, *localhost*.

.. versionchanged:: 2.9.0
   :pr:`125`: each numeric option has a range, given with the option below. A value outside it, or
   one that is not a number of the option's kind, stops the command before it starts, with one line
   naming the option, the range and the value (exit status 2). ``-b 0``, ``-c`` above 32,767 and
   ``-m`` of ``8388608TB`` (2\ :sup:`63` bytes) or more are refused.

.. include:: ../_include/submit_help.rst
