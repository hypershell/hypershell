Arguments
^^^^^^^^^

ID
    Unique task UUID.

Options
^^^^^^^

``-n``, ``--interval`` *SEC*
    Time to wait between polling (default: 5).

    Specify a number of seconds, units ``d``, ``h``, ``m`` and ``s`` in that order, whole or
    fractional (e.g., ``30s``, ``5m``, ``1h30m``, ``1.5h``), or a Slurm time ``MM:SS``,
    ``HH:MM:SS``, ``D-HH``, ``D-HH:MM`` or ``D-HH:MM:SS`` (e.g., ``1:30``). A fraction of a
    second is rounded up to the next whole second.

``-i``, ``--info``
    Print task info after completion.

    See ``hs info`` command.

``-f``, ``--format`` *FORMAT*
    Format task info ([normal], card, json, yaml).

``--json``
    Format metadata output as JSON.

``--yaml``
    Format metadata output as YAML.

``-s``, ``--status``
    Print task exit status only.

``-r``, ``--return``
    Use exit status from task.
