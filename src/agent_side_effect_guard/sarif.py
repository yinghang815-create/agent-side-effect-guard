from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .models import Finding


def to_sarif(findings: Iterable[Finding], version: str) -> dict[str, Any]:
    items = list(findings)
    rules = {
        item.rule_id: {
            "id": item.rule_id,
            "shortDescription": {"text": item.message},
            "help": {"text": item.remediation or item.message},
        }
        for item in items
    }
    return {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {"name": "agent-side-effect-guard", "version": version, "rules": list(rules.values())}
                },
                "results": [
                    {
                        "ruleId": item.rule_id,
                        "level": "error" if item.severity.name in {"HIGH", "CRITICAL"} else "warning",
                        "message": {"text": item.message},
                        "locations": [{"physicalLocation": {"artifactLocation": {"uri": item.path}}}],
                        "properties": {"severity": item.severity.name.lower(), "step": item.step},
                    }
                    for item in items
                ],
            }
        ],
    }
