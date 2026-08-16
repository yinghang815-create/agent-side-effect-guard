# Runtime journal

`SideEffectGuard` stores one row per `(operation, idempotency_key)` in SQLite. `BEGIN IMMEDIATE` serializes competing reservations. A payload SHA-256 digest prevents accidental reuse of a key for a different request.

Reservation outcomes:

- `execute`: the caller owns a live lease and may perform the side effect;
- `in_progress`: another worker owns the lease;
- `duplicate`: the operation already succeeded and the stored result is returned;
- `conflict`: the same key was used with a different payload.

Failed operations and expired leases may be retried with an incremented attempt number. SQLite coordinates processes on one machine or shared filesystem; use a transactional network database when workers cannot share the same database file.
