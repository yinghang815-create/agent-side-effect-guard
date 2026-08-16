from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TypeVar

from .models import Reservation

T = TypeVar("T")


class IdempotencyConflict(RuntimeError):
    """The same operation/key was reused with a different payload."""


class OperationInProgress(RuntimeError):
    """Another worker owns a live lease for this operation/key."""


def _json_text(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def payload_digest(payload: Any) -> str:
    if isinstance(payload, bytes):
        raw = payload
    elif isinstance(payload, str):
        raw = payload.encode("utf-8")
    else:
        raw = _json_text(payload).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class SideEffectGuard:
    """SQLite-backed exactly-once decision journal for external side effects."""

    def __init__(self, database: str | Path, *, timeout: float = 5.0) -> None:
        self.database = str(database)
        self.timeout = timeout
        Path(self.database).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database, timeout=self.timeout)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS side_effects (
                    operation TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('started', 'succeeded', 'failed')),
                    attempt INTEGER NOT NULL DEFAULT 1,
                    lease_expires REAL,
                    result_json TEXT,
                    error TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (operation, idempotency_key)
                )
                """
            )

    def reserve(
        self,
        operation: str,
        key: str,
        *,
        payload: Any = None,
        lease_seconds: float = 300.0,
    ) -> Reservation:
        if not operation.strip() or not key.strip():
            raise ValueError("operation and key must be non-empty")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        digest = payload_digest(payload)
        now = time.time()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM side_effects WHERE operation = ? AND idempotency_key = ?",
                (operation, key),
            ).fetchone()
            if row is None:
                connection.execute(
                    """
                    INSERT INTO side_effects (
                        operation, idempotency_key, payload_hash, status, attempt,
                        lease_expires, created_at, updated_at
                    ) VALUES (?, ?, ?, 'started', 1, ?, ?, ?)
                    """,
                    (operation, key, digest, now + lease_seconds, now, now),
                )
                return Reservation(operation, key, "execute", True, 1)

            if row["payload_hash"] != digest:
                return Reservation(
                    operation,
                    key,
                    "conflict",
                    False,
                    int(row["attempt"]),
                    message="idempotency key was previously used with a different payload",
                )
            if row["status"] == "succeeded":
                result = json.loads(row["result_json"]) if row["result_json"] is not None else None
                return Reservation(operation, key, "duplicate", False, int(row["attempt"]), result=result)
            if row["status"] == "started" and (row["lease_expires"] or 0) > now:
                return Reservation(
                    operation,
                    key,
                    "in_progress",
                    False,
                    int(row["attempt"]),
                    message="another worker owns the active lease",
                )

            attempt = int(row["attempt"]) + 1
            connection.execute(
                """
                UPDATE side_effects
                SET status = 'started', attempt = ?, lease_expires = ?, error = NULL, updated_at = ?
                WHERE operation = ? AND idempotency_key = ?
                """,
                (attempt, now + lease_seconds, now, operation, key),
            )
            return Reservation(operation, key, "execute", True, attempt)

    def complete(self, operation: str, key: str, *, result: Any = None) -> None:
        encoded = _json_text(result)
        now = time.time()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE side_effects
                SET status = 'succeeded', result_json = ?, lease_expires = NULL, updated_at = ?
                WHERE operation = ? AND idempotency_key = ? AND status = 'started'
                """,
                (encoded, now, operation, key),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"no started reservation for {operation}/{key}")

    def fail(self, operation: str, key: str, *, error: str) -> None:
        now = time.time()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE side_effects
                SET status = 'failed', error = ?, lease_expires = NULL, updated_at = ?
                WHERE operation = ? AND idempotency_key = ? AND status = 'started'
                """,
                (error, now, operation, key),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"no started reservation for {operation}/{key}")

    def inspect(self, operation: str, key: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM side_effects WHERE operation = ? AND idempotency_key = ?",
                (operation, key),
            ).fetchone()
        if row is None:
            return None
        data = dict(row)
        data["result"] = json.loads(data.pop("result_json")) if data["result_json"] is not None else None
        return data

    def run(
        self,
        operation: str,
        key: str,
        function: Callable[..., T],
        *args: Any,
        payload: Any = None,
        lease_seconds: float = 300.0,
        **kwargs: Any,
    ) -> T:
        reservation = self.reserve(operation, key, payload=payload, lease_seconds=lease_seconds)
        if reservation.status == "duplicate":
            return reservation.result
        if reservation.status == "conflict":
            raise IdempotencyConflict(reservation.message)
        if reservation.status == "in_progress":
            raise OperationInProgress(reservation.message)
        try:
            result = function(*args, **kwargs)
        except Exception as exc:
            self.fail(operation, key, error=f"{type(exc).__name__}: {exc}")
            raise
        self.complete(operation, key, result=result)
        return result
