# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0
"""Mutate a spec/<slug>/TECH.md FSM by regenerating its frontmatter block.

The ``hs-build`` and ``hs-review`` skills call this to advance state rather than
hand-editing YAML (surgical in-place YAML edits corrupt indentation). It parses
the frontmatter, applies the requested mutations, re-serializes the frontmatter
canonically, and writes the file back with the body preserved verbatim.

Usage examples:
    # mark a phase done and advance the pointer, stamping today's date
    uv run python .agents/factory/bin/set_phase.py spec/<slug>/TECH.md \
        --phase P2 --phase-status done --current P3 --touch

    # set an in_progress phase, update the hill honesty signal
    uv run python .agents/factory/bin/set_phase.py spec/<slug>/TECH.md \
        --phase P3 --phase-status in_progress --hill uphill --touch

    # record a failed verify attempt (the durable circuit-breaker counter)
    uv run python .agents/factory/bin/set_phase.py spec/<slug>/TECH.md \
        --phase P3 --record-attempt --touch

    # record a blocked state from a failed review
    uv run python .agents/factory/bin/set_phase.py spec/<slug>/TECH.md \
        --top-status blocked --blocked-reason "review: R2 gap" --touch

    # record a review verdict (auto-increments the review.cycle counter)
    uv run python .agents/factory/bin/set_phase.py spec/<slug>/TECH.md \
        --verdict approved --reviewed-commit abc1234 --touch

    # append a remediation phase through the validated serializer (never hand-edit the YAML)
    uv run python .agents/factory/bin/set_phase.py spec/<slug>/TECH.md \
        --add-phase P7 --name "F1 remediation: full covering index" \
        --satisfies R17 --depends-on P6 --after P6 \
        --verify "uv run pytest -v -k index" \
        --current P7 --top-status in_progress --touch

    # retune a reopened phase's body in place (remediation tightening a too-weak gate)
    uv run python .agents/factory/bin/set_phase.py spec/<slug>/TECH.md \
        --phase P3 --verify "uv run pytest -v -k task_slot" --touch

Exit codes: 0 ok · 2 parse/validation error · 3 unknown --phase/--after id.
"""
from __future__ import annotations

# Standard libs
import argparse
import sys
from pathlib import Path

# Internal libs
from _fsm import (
    FSMError,
    PHASE_STATUSES,
    TOP_STATUSES,
    dump_document,
    split_frontmatter,
    today,
    validate,
)


# Public interface
__all__ = ["main"]


def _csv_list(value: str | None) -> list[str]:
    """Parse a comma-separated flag into a stripped, empty-free list ('' -> [])."""
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def _parse_args(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Regenerate a TECH.md FSM frontmatter block.")
    ap.add_argument("path", help="path to spec/<slug>/TECH.md")
    ap.add_argument("--phase", help="phase id to mutate (e.g. P2)")
    ap.add_argument("--phase-status", choices=sorted(PHASE_STATUSES), help="new status for --phase")
    ap.add_argument("--hill", choices=["uphill", "crest", "downhill"], help="risk/honesty signal for --phase")
    ap.add_argument("--record-attempt", action="store_true",
                    help="increment --phase's failed-verify attempts counter (durable circuit breaker)")
    ap.add_argument("--add-phase", metavar="ID", help="add a new pending phase with this id (requires --name and --verify)")
    ap.add_argument("--name", help="phase name (for --add-phase, or to rename an existing --phase)")
    ap.add_argument("--satisfies", help="comma-separated GOAL R-IDs (for --add-phase or an existing --phase; '' clears)")
    ap.add_argument("--depends-on", dest="depends_on", help="comma-separated prerequisite phase ids (for --add-phase or an existing --phase; '' clears)")
    ap.add_argument("--after", help="insert the new phase after this phase id (default: append last; --add-phase only)")
    ap.add_argument("--verify", help="verify command (for --add-phase, or to retune an existing --phase's gate)")
    ap.add_argument("--current", help="set current_phase pointer (phase id, '' , or 'done')")
    ap.add_argument("--top-status", choices=sorted(TOP_STATUSES), help="set top-level status")
    ap.add_argument("--verdict", choices=["none", "changes-requested", "approved"], help="set review.verdict")
    ap.add_argument("--reviewed-commit", help="set review.last_reviewed_commit")
    ap.add_argument("--blocked-reason", help="set review.blocked_reason")
    ap.add_argument("--touch", action="store_true", help="set last_updated to today")
    return ap.parse_args(argv)


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    path = Path(args.path)
    try:
        text = path.read_text(encoding="utf-8")
        data, body = split_frontmatter(text)
    except (OSError, FSMError) as exc:
        print(f"{path}: {exc}", file=sys.stderr)
        return 2

    if args.add_phase:
        if not args.name or not args.verify:
            print("--add-phase requires --name and --verify", file=sys.stderr)
            return 2
        new_phase = {
            "id": args.add_phase,
            "name": args.name,
            "status": "pending",
            "satisfies": _csv_list(args.satisfies),
            "depends_on": _csv_list(args.depends_on),
            "parallel": False,
            "hammerable": False,
            "hill": "uphill",
            "verify": args.verify,
        }
        phases = data.setdefault("phases", [])
        if args.after:
            idx = next((i for i, p in enumerate(phases)
                        if isinstance(p, dict) and p.get("id") == args.after), None)
            if idx is None:
                print(f"{path}: unknown --after phase id {args.after!r}", file=sys.stderr)
                return 3
            phases.insert(idx + 1, new_phase)
        else:
            phases.append(new_phase)
    elif args.after:
        # --after is a position, meaningless without a phase to insert.
        print("--after requires --add-phase", file=sys.stderr)
        return 2
    elif (args.name or args.satisfies or args.depends_on or args.verify) and not args.phase:
        print("--name/--satisfies/--depends-on/--verify require --add-phase or --phase", file=sys.stderr)
        return 2

    if args.phase:
        phases = data.get("phases") or []
        target = next((p for p in phases if isinstance(p, dict) and p.get("id") == args.phase), None)
        if target is None:
            print(f"{path}: unknown phase id {args.phase!r}", file=sys.stderr)
            return 3
        if args.phase_status:
            target["status"] = args.phase_status
        if args.hill:
            target["hill"] = args.hill
        if args.record_attempt:
            target["attempts"] = int(target.get("attempts") or 0) + 1
        # A reopened phase can retune its body in place — remediation tightening a stale
        # gate. name/verify may not be blanked; empty --satisfies/--depends-on clears the list.
        if args.name is not None:
            if not args.name:
                print("--name cannot be set empty", file=sys.stderr)
                return 2
            target["name"] = args.name
        if args.verify is not None:
            if not args.verify:
                print("--verify cannot be set empty", file=sys.stderr)
                return 2
            target["verify"] = args.verify
        if args.satisfies is not None:
            target["satisfies"] = _csv_list(args.satisfies)
        if args.depends_on is not None:
            target["depends_on"] = _csv_list(args.depends_on)
    elif args.phase_status or args.hill or args.record_attempt:
        print("--phase-status/--hill/--record-attempt require --phase", file=sys.stderr)
        return 2

    if args.current is not None:
        data["current_phase"] = args.current
    if args.top_status:
        data["status"] = args.top_status
    if args.verdict or args.reviewed_commit is not None or args.blocked_reason is not None:
        review = data.get("review") or {}
        if not isinstance(review, dict):
            review = {}
        if args.verdict:
            review["verdict"] = args.verdict
            # A verdict marks one completed review pass; the counter backs the bounded loop.
            review["cycle"] = int(review.get("cycle") or 0) + 1
        if args.reviewed_commit is not None:
            review["last_reviewed_commit"] = args.reviewed_commit
        if args.blocked_reason is not None:
            review["blocked_reason"] = args.blocked_reason
        data["review"] = review
    if args.touch:
        data["last_updated"] = today()

    errors = validate(data)
    if errors:
        print(f"{path}: refusing to write — resulting frontmatter is invalid:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 2

    path.write_text(dump_document(data, body), encoding="utf-8")
    print(f"updated {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
