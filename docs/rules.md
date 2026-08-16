# Static audit rules

| Rule | Severity | Detects |
|---|---|---|
| ASG000 | Medium/High | Invalid workflow structure or parsing failure |
| ASG001 | Critical | Unbounded retries around a side effect |
| ASG002 | Medium-Critical | Retried side effect without an idempotency key |
| ASG003 | High | Constant idempotency key reused across runs |
| ASG004 | High/Critical | Destructive or financial action without confirmation |
| ASG005 | High | Side-effect failure configured to continue silently |
| ASG006 | Medium | Retry policy catches every error type |
| ASG007 | High | Concurrent side-effect runs without a deduplication key |

The audit is intentionally deterministic. Customize workflow fields before execution so the guard can see stable operation keys, retry limits, and confirmation requirements.
