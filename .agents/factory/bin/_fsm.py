# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0
"""Shared helpers for the HyperShell software-factory FSM scripts.

The finite-state machine for a feature lives in the YAML frontmatter of its
``spec/<slug>/TECH.md`` roadmap. These helpers read, validate, mutate, and
re-serialize that frontmatter so the *scripts* (not the model) own the fragile
YAML arithmetic — model in-context YAML editing is the primary FSM-corruption
risk (see ``.agents/factory/methodology.md``).

Requires PyYAML, which is present in the project uv environment; the scripts are
meant to be run as ``uv run python .agents/factory/bin/<script>.py``.
"""
from __future__ import annotations

# Standard libs
import datetime
from typing import Any

# External libs
try:
    import yaml
except ModuleNotFoundError as exc:  # pragma: no cover - environment guard
    raise SystemExit(
        "PyYAML is required. Run these scripts via `uv run python "
        ".agents/factory/bin/<script>.py` so the project environment is active."
    ) from exc


# Public interface
__all__ = [
    "FSMError",
    "REQUIRED_TOP",
    "PHASE_STATUSES",
    "TOP_STATUSES",
    "FIELD_ORDER",
    "split_frontmatter",
    "dump_document",
    "validate",
    "compute_next",
    "today",
]


REQUIRED_TOP = ["slug", "kind", "appetite", "status", "branch", "base", "current_phase", "phases"]
PHASE_STATUSES = {"pending", "in_progress", "done", "blocked"}
TOP_STATUSES = {"planned", "in_progress", "blocked", "in_review", "done"}

# Canonical key order for deterministic re-serialization (keys not listed keep
# their existing relative order, appended after these).
FIELD_ORDER = [
    "slug", "title", "kind", "appetite", "status", "branch", "base",
    "current_phase", "last_updated", "phases", "review",
]
PHASE_FIELD_ORDER = [
    "id", "name", "status", "satisfies", "depends_on",
    "parallel", "hammerable", "hill", "verify",
]


class FSMError(Exception):
    """Raised on a malformed or invalid TECH.md frontmatter."""


def today() -> str:
    """Return today's date as an ISO-8601 string (local)."""
    return datetime.date.today().isoformat()


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split a markdown document into (frontmatter dict, body string).

    The document must open with a ``---`` fence, contain a YAML block, and close
    the block with a line that is exactly ``---``. Everything after is the body.
    """
    if not text.startswith("---"):
        raise FSMError("TECH.md must begin with a '---' YAML frontmatter fence.")
    lines = text.splitlines(keepends=True)
    # lines[0] is the opening fence; find the closing fence.
    for i in range(1, len(lines)):
        if lines[i].rstrip("\n") == "---":
            fm_text = "".join(lines[1:i])
            body = "".join(lines[i + 1:])
            break
    else:
        raise FSMError("Unterminated frontmatter: no closing '---' fence found.")
    try:
        data = yaml.safe_load(fm_text)
    except yaml.YAMLError as exc:
        raise FSMError(f"Frontmatter is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise FSMError("Frontmatter did not parse to a mapping.")
    return data, body


def _ordered(data: dict[str, Any], order: list[str]) -> dict[str, Any]:
    """Return a new dict with keys in `order` first, then any remaining keys."""
    out: dict[str, Any] = {}
    for key in order:
        if key in data:
            out[key] = data[key]
    for key in data:
        if key not in out:
            out[key] = data[key]
    return out


def dump_document(data: dict[str, Any], body: str) -> str:
    """Re-serialize (frontmatter dict, body) into a full markdown document.

    Serialization is canonical and deterministic: top-level and per-phase keys
    are emitted in a fixed order; formatting is normalized (inline comments in
    the source frontmatter are dropped — enums are documented in the template
    and ``methodology.md``). The body is preserved verbatim.
    """
    data = _ordered(dict(data), FIELD_ORDER)
    phases = data.get("phases")
    if isinstance(phases, list):
        data["phases"] = [
            _ordered(dict(p), PHASE_FIELD_ORDER) if isinstance(p, dict) else p
            for p in phases
        ]
    fm = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False)
    if not body.startswith("\n"):
        body = "\n" + body
    return f"---\n{fm}---{body}"


def validate(data: dict[str, Any]) -> list[str]:
    """Return a list of human-readable validation errors (empty == valid)."""
    errors: list[str] = []
    for key in REQUIRED_TOP:
        if key not in data:
            errors.append(f"missing required top-level key: {key}")
    if data.get("status") not in TOP_STATUSES and "status" in data:
        errors.append(f"top-level status {data.get('status')!r} not in {sorted(TOP_STATUSES)}")
    phases = data.get("phases")
    if not isinstance(phases, list) or not phases:
        errors.append("phases must be a non-empty list")
        return errors
    ids: set[str] = set()
    for idx, p in enumerate(phases):
        if not isinstance(p, dict):
            errors.append(f"phase[{idx}] is not a mapping")
            continue
        pid = p.get("id")
        if not pid:
            errors.append(f"phase[{idx}] missing id")
            continue
        if pid in ids:
            errors.append(f"duplicate phase id: {pid}")
        ids.add(pid)
        if p.get("status") not in PHASE_STATUSES:
            errors.append(f"phase {pid} status {p.get('status')!r} not in {sorted(PHASE_STATUSES)}")
    for p in phases:
        if isinstance(p, dict):
            for dep in p.get("depends_on") or []:
                if dep not in ids:
                    errors.append(f"phase {p.get('id')} depends_on unknown phase {dep}")
    cur = data.get("current_phase")
    if cur and cur not in ids and cur not in ("", "done"):
        errors.append(f"current_phase {cur!r} is not a known phase id")
    return errors


def compute_next(data: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    """Compute the next actionable phase from phase statuses (authoritative).

    A phase is actionable if its status is pending/in_progress and every phase in
    its ``depends_on`` is done. Returns (phase_dict_or_None, warnings). Warnings
    flag blocked phases and any drift between the stored ``current_phase`` pointer
    and the computed next phase (crash-safety reconciliation signal).
    """
    warnings: list[str] = []
    phases = data.get("phases") or []
    status_by_id = {p.get("id"): p.get("status") for p in phases if isinstance(p, dict)}

    for p in phases:
        if isinstance(p, dict) and p.get("status") == "blocked":
            warnings.append(f"phase {p.get('id')} is blocked")

    nxt: dict[str, Any] | None = None
    for p in phases:
        if not isinstance(p, dict):
            continue
        if p.get("status") in ("pending", "in_progress"):
            deps = p.get("depends_on") or []
            unmet = [d for d in deps if status_by_id.get(d) != "done"]
            if unmet:
                continue
            nxt = p
            break

    stored = data.get("current_phase")
    if nxt is not None and stored not in (nxt.get("id"), None, ""):
        warnings.append(
            f"current_phase pointer {stored!r} != computed next {nxt.get('id')!r} "
            "(reconcile before acting)"
        )
    if nxt is None and stored not in ("", "done", None):
        warnings.append(f"no actionable phase but current_phase is {stored!r}")
    return nxt, warnings
