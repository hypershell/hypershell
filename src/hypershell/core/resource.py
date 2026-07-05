# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Resource monitoring tools."""


# Type annotations
from __future__ import annotations
from typing import Final, Dict, List, Tuple, Set

# Standard libs
from datetime import datetime
from threading import Lock
from collections import defaultdict

# External libs
from psutil import Process, NoSuchProcess, virtual_memory, cpu_count, cpu_percent as _cpu_percent

# Public interface
__all__ = [
    'CPU_COUNT', 'MEMORY_TOTAL',
    'cpu_percent', 'cpu_memory_percent', 'cpu_memory_actual',
    'get_processes', 'clear_processes',
    'check_resources',
]


# Cached for later use
CPU_COUNT: Final[int] = cpu_count() or 1
MEMORY_TOTAL: Final[int] = virtual_memory().total


# Registry and lock for Process object cache.
# We cache Process objects so we reuse them after pre-initializing them.
# The first time you call .cpu_percent() it returns 0.0 and begins the sample interval.
# We cannot use Process objects directly returned by .children() as they are re-initialized.
# So we merely use this to discover their process ID and look up cached objects if possible.
# Lastly, we store the known PIDs of children to avoid race conditions during cleanup.
process_registry: Dict[int, Process] = {}
process_registry_mapping: Dict[int, Set[int]] = defaultdict(set)
process_lock: Lock = Lock()




def register_process(p: Process) -> Process:
    """Register process in the registry."""
    p.cpu_percent(interval=0)
    process_registry[p.pid] = p
    return p


def cached_process(p: Process) -> Process:
    """Prepare process with initialization if necessary."""
    try:
        return process_registry[p.pid]
    except KeyError:
        return register_process(p)


def get_process(pid: int) -> Tuple[bool, Process]:
    """Prepare process with initialization if necessary."""
    try:
        return True, process_registry[pid]
    except KeyError:
        return False, register_process(Process(pid))


def get_processes(pid: int) -> Tuple[bool, List[Process]]:
    """List of parent process (0-th item) and all it's children (1-nth item)."""
    with process_lock:
        try:
            ready, p = get_process(pid)
            processes = [p, ]
            for child in p.children(recursive=True):
                processes.append(cached_process(child))
                process_registry_mapping[pid].add(child.pid)
            return ready, processes
        except NoSuchProcess:
            return False, []


def clear_processes(pid: int) -> None:
    """Clear process and its children from the registry."""
    with process_lock:
        process_registry.pop(pid, None)
        for child_pid in process_registry_mapping.pop(pid, {}):
            process_registry.pop(child_pid, None)


def check_resources(pid: int) -> Tuple[datetime, float, int]:
    """Check combined CPU and memory usage of a process and its children."""
    ready, processes = get_processes(pid)
    if not ready:
        # We only just initialized the objects and would get these values anyway,
        # So we don't want to actually sample them again.
        return datetime.now(), 0.0, 0
    try:
        return datetime.now(), cpu_percent(processes), cpu_memory_actual(processes)
    except NoSuchProcess:
        return datetime.now(), 0.0, 0  # Process may have disappeared since last call


def cpu_percent(processes: List[Process] = None) -> float:
    """Compute overall CPU utilization for system or specific process (1.0 means 100% of a core)."""
    if not processes:
        return _cpu_percent(interval=0) / 100.0  # System-wide
    else:
        return sum(p.cpu_percent(interval=0) for p in processes) / 100.0


def cpu_memory_percent(processes: List[Process] = None) -> float:
    """Percent total CPU memory used by system or specific process."""
    if not processes:
        return virtual_memory().percent  # System-wide
    else:
        return sum(p.memory_percent() for p in processes)


def cpu_memory_actual(processes: List[Process] = None) -> int:
    """Actual CPU memory used by system or specific process in bytes."""
    if not processes:
        return virtual_memory().used  # System-wide
    else:
        return int((cpu_memory_percent(processes) / 100.0) * MEMORY_TOTAL)
