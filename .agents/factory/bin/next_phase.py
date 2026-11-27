# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0
"""Print the next actionable phase of a spec/<slug>/TECH.md FSM as JSON.

The ``hs-build`` skill runs this at the top of every invocation instead of
parsing the YAML frontmatter itself. The emitted JSON is the ground truth for
"what do I do next"; the model executes, the script computes the transition.

Usage:
    uv run python .agents/factory/bin/next_phase.py spec/<slug>/TECH.md
    uv run python .agents/factory/bin/next_phase.py --all   # portfolio view over spec/*/TECH.md
                                                            # (run from the repo root)

Exit codes: 0 ok · 2 parse/validation error (message on stderr). With ``--all`` a corrupt
file becomes an ``error`` entry in the report rather than a failure — the portfolio view
must not die on one bad record.
"""
from __future__ import annotations

# Standard libs
import json
import sys
from pathlib import Path

# Internal libs
from _fsm import FSMError, compute_next, split_frontmatter, validate


# Public interface
__all__ = ["main"]


def _summary(path: Path) -> dict:
    """One portfolio row for --all: slug, status, verdict, next phase (or an error)."""
    try:
        data, _body = split_frontmatter(path.read_text(encoding="utf-8"))
    except (OSError, FSMError) as exc:
        return {"path": str(path), "error": str(exc)}
    errors = validate(data)
    if errors:
        return {"path": str(path), "error": "; ".join(errors)}
    nxt, warnings = compute_next(data)
    review = data.get("review") or {}
    return {
        "slug": data.get("slug"),
        "kind": data.get("kind"),
        "top_status": data.get("status"),
        "verdict": review.get("verdict"),
        "cycle": review.get("cycle", 0),
        "next_phase": (nxt or {}).get("id"),
        "warnings": warnings,
        "path": str(path),
    }


def main(argv: list[str]) -> int:
    if argv == ["--all"]:
        rows = [_summary(p) for p in sorted(Path("spec").glob("*/TECH.md"))]
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return 0
    if len(argv) != 1:
        print("usage: next_phase.py spec/<slug>/TECH.md | --all", file=sys.stderr)
        return 2
    path = Path(argv[0])
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"cannot read {path}: {exc}", file=sys.stderr)
        return 2
    try:
        data, _body = split_frontmatter(text)
    except FSMError as exc:
        print(f"{path}: {exc}", file=sys.stderr)
        return 2

    errors = validate(data)
    if errors:
        print(f"{path}: invalid TECH.md frontmatter:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 2

    nxt, warnings = compute_next(data)
    phases = data.get("phases") or []
    counts: dict[str, int] = {s: 0 for s in ("pending", "in_progress", "done", "blocked")}
    for p in phases:
        if isinstance(p, dict):
            counts[p.get("status", "pending")] = counts.get(p.get("status", "pending"), 0) + 1

    report = {
        "slug": data.get("slug"),
        "title": data.get("title"),
        "kind": data.get("kind"),
        "appetite": data.get("appetite"),
        "branch": data.get("branch"),
        "base": data.get("base"),
        "top_status": data.get("status"),
        "current_phase": data.get("current_phase"),
        "review": data.get("review"),
        "counts": counts,
        "total_phases": len([p for p in phases if isinstance(p, dict)]),
        "all_done": nxt is None and counts["blocked"] == 0,
        "next_phase": nxt,
        "warnings": warnings,
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
