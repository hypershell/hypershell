# SPDX-FileCopyrightText: 2025 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Resource monitoring tools."""


# Type annotations
from __future__ import annotations
from typing import Final, Dict, List, Tuple

# Standard libs
from datetime import datetime
from threading import Lock

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
CPU_COUNT: Final[int] = cpu_count()
MEMORY_TOTAL: Final[int] = virtual_memory().total


# Registry and lock for Process object cache.
# We cache Process objects so we reuse them after pre-initializing them.
# The first time you call .cpu_percent() it returns 0.0 and begins the sample interval.
# We cannot use Process objects directly returned by .children() as they are re-initialized.
process_registry: Dict[int, Process] = {}
process_lock: Lock = Lock()


def get_processes(pid: int) -> Tuple[bool, List[Process]]:
    """List of parent process (0-th item) and all it's children (1-nth item)."""
    with process_lock:
        try:
            ready = False
            processes = []
            if pid not in process_registry:
                process = Process(pid=pid)
                process.cpu_percent(interval=0)
                process_registry[pid] = process
                processes.append(process)
            else:
                processes.append(process_registry[pid])
                ready = True
            for child in processes[0].children(recursive=True):
                if child.pid not in process_registry:
                    child.cpu_percent(interval=0)
                    process_registry[child.pid] = child
                    processes.append(child)
                else:
                    processes.append(process_registry[child.pid])
            return ready, processes
        except NoSuchProcess:
            return False, []


def clear_processes(pid: int) -> None:
    """Clear process and its children from the registry."""
    with process_lock:
        if process := process_registry.pop(pid, None):
            for child in process.children(recursive=True):
                process_registry.pop(child.pid, None)


def check_resources(pid: int) -> Tuple[datetime, float, int]:
    """Check combined CPU and memory usage of a process and its children."""
    ready, processes = get_processes(pid)
    if not ready:
        # We only just initialized the objects and would get these values anyway
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
