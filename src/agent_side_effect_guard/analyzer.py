from __future__ import annotations

import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import tomllib

from .models import Finding, Severity

CATEGORIES: tuple[tuple[str, re.Pattern[str], Severity], ...] = (
    (
        "financial",
        re.compile(r"(?:^|[_./-])(charge|pay|purchase|refund|transfer)(?:$|[_./-])", re.I),
        Severity.CRITICAL,
    ),
    (
        "destructive",
        re.compile(r"(?:^|[_./-])(delete|destroy|drop|purge|remove|terminate|wipe)(?:$|[_./-])", re.I),
        Severity.HIGH,
    ),
    (
        "external",
        re.compile(r"(?:^|[_./-])(deploy|email|message|post|publish|send|upload)(?:$|[_./-])", re.I),
        Severity.HIGH,
    ),
    ("write", re.compile(r"(?:^|[_./-])(create|edit|modify|patch|update|write)(?:$|[_./-])", re.I), Severity.MEDIUM),
)
UNBOUNDED = {-1, 0, "-1", "0", "forever", "infinite", "infinity", "unbounded", "unlimited"}


def _steps(document: dict[str, Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(document.get("steps"), list):
        found.extend(step for step in document["steps"] if isinstance(step, dict))
    jobs = document.get("jobs", {})
    if isinstance(jobs, dict):
        for job in jobs.values():
            if isinstance(job, dict) and isinstance(job.get("steps"), list):
                found.extend(step for step in job["steps"] if isinstance(step, dict))
    if isinstance(document.get("nodes"), list):
        found.extend(step for step in document["nodes"] if isinstance(step, dict))
    return found


def _identity(step: dict[str, Any], index: int) -> tuple[str, str]:
    action = str(step.get("action") or step.get("tool") or step.get("uses") or step.get("type") or "")
    name = str(step.get("id") or step.get("name") or action or f"step-{index + 1}")
    return name, action or name


def _category(action: str) -> tuple[str, Severity] | None:
    for name, pattern, severity in CATEGORIES:
        if pattern.search(action.replace(" ", "_")):
            return name, severity
    return None


def _value(step: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in step:
            return step[key]
    inputs = step.get("with", {})
    if isinstance(inputs, dict):
        for key in keys:
            if key in inputs:
                return inputs[key]
    return None


def _attempts(step: dict[str, Any]) -> Any:
    retry = step.get("retry")
    if isinstance(retry, dict):
        return retry.get("max_attempts", retry.get("maxAttempts", 2))
    if retry is True:
        return 2
    if retry in (False, None):
        return 1
    return retry


def _is_dynamic_key(value: Any) -> bool:
    text = str(value or "")
    return any(marker in text for marker in ("${", "{{", "event.", "request.", "run_id", "operation_id"))


def analyze_document(document: Any, *, path: str = "<memory>") -> list[Finding]:
    if not isinstance(document, dict):
        return [Finding("ASG000", Severity.HIGH, "workflow root must be an object", path)]
    steps = _steps(document)
    if not steps:
        return [Finding("ASG000", Severity.MEDIUM, "no workflow steps found", path)]

    findings: list[Finding] = []
    has_side_effect = False
    for index, step in enumerate(steps):
        name, action = _identity(step, index)
        category = _category(action)
        if category is None:
            continue
        has_side_effect = True
        category_name, severity = category
        attempts = _attempts(step)
        key = _value(step, "idempotency_key", "idempotencyKey", "operation_id", "operationId")
        confirmation = _value(step, "confirmation", "require_confirmation", "requiresApproval", "approval")

        if attempts in UNBOUNDED:
            findings.append(
                Finding(
                    "ASG001",
                    Severity.CRITICAL,
                    "side-effect step has unbounded retries",
                    path,
                    name,
                    "Use a small finite retry limit and a durable idempotency key.",
                )
            )
        try:
            retried = int(attempts) > 1
        except (TypeError, ValueError):
            retried = bool(attempts)
        if retried and not key:
            findings.append(
                Finding(
                    "ASG002",
                    severity,
                    f"retried {category_name} step has no idempotency key",
                    path,
                    name,
                    "Derive a stable key from the originating event or business operation.",
                )
            )
        if key and not _is_dynamic_key(key):
            findings.append(
                Finding(
                    "ASG003",
                    Severity.HIGH,
                    "idempotency key appears constant across runs",
                    path,
                    name,
                    "Include a stable event, request, or operation identifier in the key.",
                )
            )
        if category_name in {"financial", "destructive"} and confirmation is not True:
            findings.append(
                Finding(
                    "ASG004",
                    severity,
                    f"{category_name} step has no explicit confirmation gate",
                    path,
                    name,
                    "Require an explicit human or policy approval before execution.",
                )
            )
        if step.get("continue_on_error") is True or step.get("continue-on-error") is True:
            findings.append(
                Finding(
                    "ASG005",
                    Severity.HIGH,
                    "side-effect failure is configured to continue silently",
                    path,
                    name,
                    "Stop the workflow or persist a durable failed state for operator review.",
                )
            )
        retry = step.get("retry", {})
        retry_on = retry.get("retry_on", retry.get("retryOn")) if isinstance(retry, dict) else None
        if retry_on in {"*", "all", "Exception", "BaseException"}:
            findings.append(
                Finding(
                    "ASG006",
                    Severity.MEDIUM,
                    "retry policy catches every error class",
                    path,
                    name,
                    "Retry only explicitly transient failures such as timeouts or rate limits.",
                )
            )

    concurrency = document.get("concurrency", {})
    if has_side_effect and isinstance(concurrency, dict):
        limit = concurrency.get("max", concurrency.get("limit", 1))
        lock_key = concurrency.get("key") or concurrency.get("group") or concurrency.get("dedupe_key")
        try:
            concurrent = int(limit) > 1
        except (TypeError, ValueError):
            concurrent = False
        if concurrent and not lock_key:
            findings.append(
                Finding(
                    "ASG007",
                    Severity.HIGH,
                    "concurrent side-effect workflow has no deduplication key",
                    path,
                    remediation="Group concurrent runs by the originating event or business operation.",
                )
            )
    return sorted(findings, key=lambda item: (-int(item.severity), item.rule_id, item.step or ""))


def analyze_path(path: str | Path) -> list[Finding]:
    workflow_path = Path(path)
    try:
        with workflow_path.open("rb") as handle:
            document = tomllib.load(handle) if workflow_path.suffix.lower() == ".toml" else json.loads(handle.read())
    except (OSError, UnicodeError, json.JSONDecodeError, tomllib.TOMLDecodeError) as exc:
        return [Finding("ASG000", Severity.HIGH, f"cannot parse workflow: {exc}", str(workflow_path))]
    return analyze_document(document, path=str(workflow_path))


def candidate_paths(paths: Iterable[str]) -> list[Path]:
    candidates: list[Path] = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            candidates.extend(sorted(item for item in path.rglob("*") if item.suffix.lower() in {".json", ".toml"}))
        else:
            candidates.append(path)
    return candidates
