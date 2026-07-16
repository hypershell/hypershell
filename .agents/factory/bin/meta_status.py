# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0
"""Print the harness-feedback findings of a spec/<slug>/META.md as JSON.

Part of the factory's self-improvement loop. The lifecycle skills append
markdown ``## F<n>`` finding sections to ``spec/<slug>/META.md`` (silence by
default); ``hs-publish`` and ``/hs-harness`` read them through this script rather
than parsing the markdown themselves. Mirrors ``next_phase.py``'s principle: the
*script* owns the fragile parsing, the model executes.

The finding format is deliberately **not** YAML (so this reader needs no PyYAML
and cannot corrupt on append): each finding is a ``## F<n> — title`` header, a
single backtick ``key=val`` metadata line, and ``- **Key:** value`` bullets. A
schema example living inside a fenced ``` code block (as in the template) is
skipped — only findings appended outside a fence are real.

Usage:
    uv run python .agents/factory/bin/meta_status.py spec/<slug>/META.md
    uv run python .agents/factory/bin/meta_status.py spec/<slug>/META.md --status open
    uv run python .agents/factory/bin/meta_status.py spec/<slug>/META.md --severity high --id F1 F3

A missing file is a valid empty state (``exists: false``, no findings, exit 0) —
a feature that logged no harness friction simply has no META.md.

Exit codes: 0 ok · 2 usage error.
"""
from __future__ import annotations

# Standard libs
import argparse
import json
import re
import sys
from pathlib import Path


# Public interface
__all__ = ["main"]


SEVERITIES = ("high", "medium", "low")
STATUSES = ("open", "applied", "rejected", "deferred")

# A finding header: '## F1 — title' (em dash, en dash, or hyphen separator, all optional).
_FINDING_RE = re.compile(r"^##\s+(F\d+)\b\s*[—–-]?\s*(.*)$")
# A '**Key:** value' pair (several may share one bullet line, split on ' · ').
_FIELD_RE = re.compile(r"\*\*(.+?):\*\*\s*(.*)")


def _parse_meta_line(line: str) -> dict[str, str]:
    """Parse a backtick `key=val key=val` metadata line into a dict."""
    out: dict[str, str] = {}
    for token in line.strip().strip("`").split():
        if "=" in token:
            key, _, val = token.partition("=")
            out[key] = val
    return out


def _parse_fields(body_lines: list[str]) -> dict[str, str]:
    """Extract '**Key:** value' pairs from a finding's bullet body."""
    fields: dict[str, str] = {}
    for raw in body_lines:
        line = raw.strip()
        if line.startswith("-"):
            line = line[1:].strip()
        for part in line.split(" · "):
            match = _FIELD_RE.match(part.strip())
            if match:
                # Drop a trailing "(qualifier)" so labels like "Skill cause (not mine)"
                # normalize to the same key as "Skill cause" — mechanism, not hand-audit.
                label = re.sub(r"\s*\([^)]*\)\s*$", "", match.group(1).strip())
                key = label.lower().replace(" ", "_")
                fields[key] = match.group(2).strip()
    return fields


def parse_findings(text: str) -> list[dict[str, object]]:
    """Parse all real (outside-a-fence) ``## F<n>`` finding sections."""
    lines = text.splitlines()
    in_fence = False
    findings: list[dict[str, object]] = []
    current: dict[str, object] | None = None
    body: list[str] = []

    def flush() -> None:
        if current is not None:
            current["fields"] = _parse_fields(body)
            findings.append(current)

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            if current is not None:
                body.append(line)
            continue
        if in_fence:
            if current is not None:
                body.append(line)
            continue
        header = _FINDING_RE.match(line) if line.startswith("##") else None
        if header:
            flush()
            current = {"id": header.group(1), "title": header.group(2).strip(), "meta": {}}
            body = []
            continue
        if line.startswith("## ") and current is not None:
            # A non-finding '## ' section closes the current finding.
            flush()
            current = None
            body = []
            continue
        if current is not None:
            if not current["meta"] and stripped.startswith("`") and "=" in stripped:
                current["meta"] = _parse_meta_line(stripped)
            else:
                body.append(line)
    flush()

    # Flatten metadata + bullet fields into a stable record.
    records: list[dict[str, object]] = []
    for f in findings:
        meta = f.get("meta", {}) or {}
        fields = f.get("fields", {}) or {}
        records.append(
            {
                "id": f["id"],
                "title": f["title"],
                "origin": meta.get("origin", ""),
                "severity": meta.get("severity", ""),
                "category": meta.get("category", ""),
                "status": meta.get("status", ""),
                "target": meta.get("target", ""),
                "what_happened": fields.get("what_happened", ""),
                "skill_cause": fields.get("skill_cause", ""),
                "recommended_fix": fields.get("recommended_fix", ""),
                "confidence": fields.get("confidence", ""),
                "effort": fields.get("effort", ""),
            }
        )
    return records


def _parse_args(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Read harness findings from a META.md as JSON.")
    ap.add_argument("path", help="path to spec/<slug>/META.md")
    ap.add_argument("--status", choices=STATUSES, help="only findings with this status")
    ap.add_argument("--severity", choices=SEVERITIES, help="only findings with this severity")
    ap.add_argument("--id", nargs="+", metavar="F#", help="only these finding ids (e.g. F1 F3)")
    return ap.parse_args(argv)


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    path = Path(args.path)

    if not path.exists():
        print(json.dumps({"path": str(path), "exists": False, "counts": {}, "findings": []}, indent=2))
        return 0

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"cannot read {path}: {exc}", file=sys.stderr)
        return 2

    findings = parse_findings(text)

    # Counts are over ALL findings (before filtering) so a caller sees the full picture.
    counts = {"total": len(findings)}
    for status in STATUSES:
        counts[status] = sum(1 for f in findings if f["status"] == status)
    for severity in SEVERITIES:
        counts[severity] = sum(1 for f in findings if f["severity"] == severity)

    selected = findings
    if args.status:
        selected = [f for f in selected if f["status"] == args.status]
    if args.severity:
        selected = [f for f in selected if f["severity"] == args.severity]
    if args.id:
        wanted = set(args.id)
        selected = [f for f in selected if f["id"] in wanted]

    report = {"path": str(path), "exists": True, "counts": counts, "findings": selected}
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
