import json
import tempfile
import unittest
from pathlib import Path

from agent_side_effect_guard.analyzer import analyze_document, analyze_path, candidate_paths
from agent_side_effect_guard.models import Severity


class AnalyzerTests(unittest.TestCase):
    def test_read_only_workflow_is_clean(self):
        workflow = {"steps": [{"id": "lookup", "action": "read_customer"}]}
        self.assertEqual(analyze_document(workflow), [])

    def test_retried_send_requires_idempotency_key(self):
        workflow = {"steps": [{"id": "send", "action": "send_email", "retry": {"max_attempts": 3}}]}
        findings = analyze_document(workflow)
        self.assertEqual(findings[0].rule_id, "ASG002")
        self.assertEqual(findings[0].severity, Severity.HIGH)

    def test_dynamic_idempotency_key_is_accepted(self):
        workflow = {
            "steps": [
                {
                    "action": "send_email",
                    "retry": {"max_attempts": 3},
                    "idempotency_key": "email:${event.id}",
                }
            ]
        }
        self.assertEqual(analyze_document(workflow), [])

    def test_constant_key_is_high(self):
        workflow = {"steps": [{"action": "post_message", "idempotency_key": "same-key"}]}
        self.assertEqual(analyze_document(workflow)[0].rule_id, "ASG003")

    def test_unbounded_retry_is_critical(self):
        workflow = {"steps": [{"action": "deploy_release", "retry": {"max_attempts": "unlimited"}}]}
        rules = {item.rule_id for item in analyze_document(workflow)}
        self.assertIn("ASG001", rules)

    def test_delete_requires_confirmation(self):
        workflow = {"steps": [{"action": "delete_account"}]}
        self.assertEqual(analyze_document(workflow)[0].rule_id, "ASG004")

    def test_confirmed_delete_is_clean(self):
        workflow = {"steps": [{"action": "delete_account", "confirmation": True}]}
        self.assertEqual(analyze_document(workflow), [])

    def test_financial_action_is_critical(self):
        workflow = {"steps": [{"action": "charge_card"}]}
        self.assertEqual(analyze_document(workflow)[0].severity, Severity.CRITICAL)

    def test_continue_on_error_is_high(self):
        workflow = {"steps": [{"action": "publish_article", "continue_on_error": True}]}
        self.assertEqual(analyze_document(workflow)[0].rule_id, "ASG005")

    def test_catch_all_retry_is_medium(self):
        workflow = {
            "steps": [
                {
                    "action": "update_record",
                    "retry": {"max_attempts": 2, "retry_on": "all"},
                    "idempotency_key": "${event.id}",
                }
            ]
        }
        self.assertEqual(analyze_document(workflow)[0].rule_id, "ASG006")

    def test_concurrency_requires_dedupe_key(self):
        workflow = {"concurrency": {"max": 4}, "steps": [{"action": "send_email"}]}
        self.assertEqual(analyze_document(workflow)[0].rule_id, "ASG007")

    def test_concurrency_group_is_accepted(self):
        workflow = {
            "concurrency": {"max": 4, "group": "${event.id}"},
            "steps": [{"action": "send_email"}],
        }
        self.assertEqual(analyze_document(workflow), [])

    def test_github_jobs_shape(self):
        workflow = {"jobs": {"publish": {"steps": [{"uses": "vendor/publish_release", "retry": True}]}}}
        self.assertEqual(analyze_document(workflow)[0].rule_id, "ASG002")

    def test_nodes_shape(self):
        workflow = {"nodes": [{"name": "pay", "tool": "pay_invoice"}]}
        self.assertEqual(analyze_document(workflow)[0].rule_id, "ASG004")

    def test_invalid_root(self):
        self.assertEqual(analyze_document([])[0].rule_id, "ASG000")

    def test_missing_steps(self):
        self.assertEqual(analyze_document({})[0].rule_id, "ASG000")

    def test_invalid_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text("{", encoding="utf-8")
            self.assertEqual(analyze_path(path)[0].rule_id, "ASG000")

    def test_directory_discovery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.json").write_text(json.dumps({}), encoding="utf-8")
            (root / "b.toml").write_text("name='x'", encoding="utf-8")
            (root / "c.txt").write_text("x", encoding="utf-8")
            self.assertEqual(len(candidate_paths([directory])), 2)


if __name__ == "__main__":
    unittest.main()
