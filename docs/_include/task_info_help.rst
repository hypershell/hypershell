Arguments
^^^^^^^^^

ID
    Unique task UUID.

Options
^^^^^^^

``-f``, ``--format`` *FORMAT*
    Format task info ([normal], json, yaml).

``--json``
    Format metadata output as JSON.

``--yaml``
    Format metadata output as YAML.

``-x``, ``--extract`` *FIELD*
    Print this field only (e.g., ``-x submit_time``).

``--stdout``
    Print <stdout> of task if captured, fetch from client if necessary.

``--stderr``
    Print <stderr> of task if captured, fetch from client if necessary.

``--perf``
    Print captured resource metrics (CSV) from task.

    Requires the task to have been run under ``--monitor``, which records per-task
    CPU and memory usage as a time series alongside the task outputs.

``-i``, ``--ignore-partitions``
    Suppress auto-union feature (SQLite only).

    When using the `sqlite` provider, all databases matching the numbering pattern
    applied by ``hs initdb --rotate`` are automatically attached as partitions and
    used within a temporary view, making full task history searchable.
