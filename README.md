# Agent Side-Effect Guard

[![CI](https://github.com/yinghang815-create/agent-side-effect-guard/actions/workflows/ci.yml/badge.svg)](https://github.com/yinghang815-create/agent-side-effect-guard/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

`agent-side-effect-guard` prevents retrying AI agents from sending, posting, paying, deploying, updating, or deleting twice. It combines a deterministic workflow linter with a dependency-free SQLite idempotency journal for Python agents.

It complements [`agent-checkpoint-sqlite`](https://github.com/yinghang815-create/agent-checkpoint-sqlite): checkpoints make agent work resumable; this guard makes external side effects safe to resume.

```text
checkpoint -> resume -> reserve side effect -> execute once -> record result
```

## Install

```bash
python -m pip install agent-side-effect-guard
```

## Audit workflows

```bash
agent-side-effect-guard audit workflow.json
agent-side-effect-guard audit . --format sarif --output results.sarif
```

The audit catches unbounded retries, missing or constant idempotency keys, absent confirmation gates, swallowed failures, broad retry policies, and unsafe concurrency. See [all rules](docs/rules.md).

## Guard a real side effect

```python
from agent_side_effect_guard import SideEffectGuard

guard = SideEffectGuard("agent-effects.db")

result = guard.run(
    "send_email",
    "welcome:user-42",
    lambda: mailer.send(template="welcome", user_id=42),
    payload={"template": "welcome", "user_id": 42},
)
```

The first caller executes the function. A retry with the same key and payload returns the stored result without calling the external service again. A changed payload raises `IdempotencyConflict`; an active worker raises `OperationInProgress`.

For manual orchestration:

```python
reservation = guard.reserve("publish", "article:123", payload={"revision": 7})
if reservation.should_execute:
    try:
        result = publish_article()
        guard.complete("publish", "article:123", result=result)
    except Exception as exc:
        guard.fail("publish", "article:123", error=str(exc))
        raise
```

Read the [runtime semantics](docs/runtime.md) before using leases with multiple workers.

## CLI journal

```bash
side-effect-guard reserve --db effects.db --operation publish --key article:123 --payload '{"revision":7}'
side-effect-guard complete --db effects.db --operation publish --key article:123 --result '{"url":"https://example.com/a"}'
side-effect-guard status --db effects.db --operation publish --key article:123
```

## GitHub Actions

```yaml
- uses: actions/setup-python@v5
  with:
    python-version: "3.12"
- run: python -m pip install agent-side-effect-guard
- run: agent-side-effect-guard audit workflows --format sarif --output side-effects.sarif
```

## Limits

Exactly-once delivery is impossible without cooperation from downstream systems. This project provides durable local decisions and payload conflict detection; still use provider idempotency keys when an API supports them. SQLite is intended for agents that share one filesystem.

Contributions are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) and use [SECURITY.md](SECURITY.md) for vulnerability reports.
