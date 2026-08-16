import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from agent_side_effect_guard.cli import main


class CliTests(unittest.TestCase):
    def test_audit_json_output(self):
        with tempfile.TemporaryDirectory() as directory:
            workflow = Path(directory) / "workflow.json"
            workflow.write_text(json.dumps({"steps": [{"action": "delete_record"}]}), encoding="utf-8")
            stream = StringIO()
            with redirect_stdout(stream):
                code = main(["audit", str(workflow), "--format", "json"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(stream.getvalue())["findings"][0]["rule_id"], "ASG004")

    def test_runtime_cli_lifecycle(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "guard.db")
            with redirect_stdout(StringIO()):
                self.assertEqual(main(["reserve", "--db", database, "--operation", "send", "--key", "e1"]), 0)
                self.assertEqual(
                    main(["complete", "--db", database, "--operation", "send", "--key", "e1", "--result", '{"id":1}']),
                    0,
                )
            stream = StringIO()
            with redirect_stdout(stream):
                self.assertEqual(main(["status", "--db", database, "--operation", "send", "--key", "e1"]), 0)
            self.assertEqual(json.loads(stream.getvalue())["status"], "succeeded")


if __name__ == "__main__":
    unittest.main()
