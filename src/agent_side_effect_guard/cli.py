from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from .analyzer import analyze_path, candidate_paths
from .journal import SideEffectGuard
from .models import Finding, Severity
from .sarif import to_sarif


def _render_text(findings: list[Finding]) -> str:
    if not findings:
        return "OK: no unsafe side-effect patterns found"
    lines = [
        f"{item.path}: {item.severity.name.lower():8} {item.rule_id} {item.message}"
        + (f" [{item.step}]" if item.step else "")
        for item in findings
    ]
    lines.append(f"\nFound {len(findings)} issue(s)")
    return "\n".join(lines)


def _json_value(raw: str | None) -> object:
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-side-effect-guard")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    audit = commands.add_parser("audit", help="scan JSON/TOML agent workflows")
    audit.add_argument("paths", nargs="+")
    audit.add_argument("--format", choices=("text", "json", "sarif"), default="text")
    audit.add_argument("--output")
    audit.add_argument("--fail-on", choices=tuple(item.name.lower() for item in Severity), default="high")

    for name in ("reserve", "complete", "fail", "status"):
        command = commands.add_parser(name, help=f"{name} a runtime journal entry")
        command.add_argument("--db", required=True)
        command.add_argument("--operation", required=True)
        command.add_argument("--key", required=True)
        if name == "reserve":
            command.add_argument("--payload")
            command.add_argument("--lease-seconds", type=float, default=300.0)
        elif name == "complete":
            command.add_argument("--result")
        elif name == "fail":
            command.add_argument("--error", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "audit":
        findings = [item for path in candidate_paths(args.paths) for item in analyze_path(path)]
        if args.format == "json":
            report = json.dumps({"version": __version__, "findings": [item.to_dict() for item in findings]}, indent=2)
        elif args.format == "sarif":
            report = json.dumps(to_sarif(findings, __version__), indent=2)
        else:
            report = _render_text(findings)
        if args.output:
            output = Path(args.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(report + "\n", encoding="utf-8")
        else:
            print(report)
        threshold = Severity.parse(args.fail_on)
        return 1 if any(item.severity >= threshold for item in findings) else 0

    guard = SideEffectGuard(args.db)
    if args.command == "reserve":
        response = guard.reserve(
            args.operation,
            args.key,
            payload=_json_value(args.payload),
            lease_seconds=args.lease_seconds,
        ).to_dict()
    elif args.command == "complete":
        guard.complete(args.operation, args.key, result=_json_value(args.result))
        response = {"status": "succeeded"}
    elif args.command == "fail":
        guard.fail(args.operation, args.key, error=args.error)
        response = {"status": "failed"}
    else:
        response = guard.inspect(args.operation, args.key) or {"status": "missing"}
    print(json.dumps(response, indent=2))
    return 2 if response.get("status") in {"conflict", "in_progress"} else 0
