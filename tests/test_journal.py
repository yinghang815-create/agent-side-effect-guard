import tempfile
import time
import unittest
from pathlib import Path

from agent_side_effect_guard.journal import (
    IdempotencyConflict,
    OperationInProgress,
    SideEffectGuard,
    payload_digest,
)


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.guard = SideEffectGuard(Path(self.temporary.name) / "guard.db")

    def tearDown(self):
        self.temporary.cleanup()

    def test_first_reservation_executes(self):
        reservation = self.guard.reserve("send_email", "event-1", payload={"to": "a@example.com"})
        self.assertTrue(reservation.should_execute)
        self.assertEqual(reservation.status, "execute")

    def test_active_lease_blocks_second_worker(self):
        self.guard.reserve("send_email", "event-1")
        reservation = self.guard.reserve("send_email", "event-1")
        self.assertEqual(reservation.status, "in_progress")

    def test_completed_operation_is_duplicate(self):
        self.guard.reserve("send_email", "event-1")
        self.guard.complete("send_email", "event-1", result={"message_id": "m1"})
        reservation = self.guard.reserve("send_email", "event-1")
        self.assertEqual(reservation.status, "duplicate")
        self.assertEqual(reservation.result["message_id"], "m1")

    def test_payload_mismatch_is_conflict(self):
        self.guard.reserve("send_email", "event-1", payload={"to": "a@example.com"})
        reservation = self.guard.reserve("send_email", "event-1", payload={"to": "b@example.com"})
        self.assertEqual(reservation.status, "conflict")

    def test_failed_operation_can_retry(self):
        self.guard.reserve("deploy", "release-1")
        self.guard.fail("deploy", "release-1", error="timeout")
        reservation = self.guard.reserve("deploy", "release-1")
        self.assertEqual(reservation.status, "execute")
        self.assertEqual(reservation.attempt, 2)

    def test_expired_lease_can_retry(self):
        self.guard.reserve("post", "item-1", lease_seconds=0.01)
        time.sleep(0.02)
        reservation = self.guard.reserve("post", "item-1")
        self.assertTrue(reservation.should_execute)
        self.assertEqual(reservation.attempt, 2)

    def test_inspect_missing_returns_none(self):
        self.assertIsNone(self.guard.inspect("send", "missing"))

    def test_inspect_returns_failed_error(self):
        self.guard.reserve("send", "event-1")
        self.guard.fail("send", "event-1", error="network")
        self.assertEqual(self.guard.inspect("send", "event-1")["error"], "network")

    def test_complete_without_reservation_fails(self):
        with self.assertRaises(KeyError):
            self.guard.complete("send", "missing")

    def test_empty_key_fails(self):
        with self.assertRaises(ValueError):
            self.guard.reserve("send", "")

    def test_non_positive_lease_fails(self):
        with self.assertRaises(ValueError):
            self.guard.reserve("send", "key", lease_seconds=0)

    def test_run_executes_only_once(self):
        calls = []

        def send():
            calls.append("sent")
            return {"id": 7}

        first = self.guard.run("send", "event-1", send)
        second = self.guard.run("send", "event-1", send)
        self.assertEqual(first, second)
        self.assertEqual(calls, ["sent"])

    def test_run_records_failure(self):
        def fail():
            raise RuntimeError("boom")

        with self.assertRaises(RuntimeError):
            self.guard.run("publish", "article-1", fail)
        self.assertEqual(self.guard.inspect("publish", "article-1")["status"], "failed")

    def test_run_raises_on_conflicting_payload(self):
        self.guard.run("send", "event-1", lambda: "ok", payload={"body": "a"})
        with self.assertRaises(IdempotencyConflict):
            self.guard.run("send", "event-1", lambda: "bad", payload={"body": "b"})

    def test_run_raises_when_in_progress(self):
        self.guard.reserve("send", "event-1")
        with self.assertRaises(OperationInProgress):
            self.guard.run("send", "event-1", lambda: "bad")

    def test_digest_is_stable_for_mapping_order(self):
        self.assertEqual(payload_digest({"a": 1, "b": 2}), payload_digest({"b": 2, "a": 1}))


if __name__ == "__main__":
    unittest.main()
