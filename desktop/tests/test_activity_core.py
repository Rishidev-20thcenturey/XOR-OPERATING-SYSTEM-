"""Activity viewer core: filtering, search, chain status, text output."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "broker", "src"))

import activity_core  # noqa: E402
from xor_broker.audit import AuditLog  # noqa: E402
from xor_broker.broker import Broker  # noqa: E402
from xor_broker.confirm import ConfirmationManager, ScriptedConfirmation  # noqa: E402
from xor_broker.manifest import parse_manifest  # noqa: E402
from xor_broker.models import ActionRequest, Operation  # noqa: E402
from xor_broker.policy import PolicyEngine  # noqa: E402


class TestActivityCore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        self.audit_path = os.path.join(root, "audit.jsonl")
        granted = os.path.join(root, "granted.txt")
        with open(granted, "w") as fh:
            fh.write("data")
        broker = Broker(
            PolicyEngine(parse_manifest({"capabilities": [{
                "capability_id": "g", "tool": "t", "operation": "read_file",
                "resource": {"type": "file", "path": granted}, "rights": ["read"]}]})),
            ConfirmationManager(ScriptedConfirmation()),
            AuditLog(self.audit_path))
        broker.handle(ActionRequest("t", Operation.READ_FILE, granted))
        broker.handle(ActionRequest("t", Operation.READ_FILE, "/etc/passwd"))
        broker.handle(ActionRequest("t", Operation.NETWORK_SEND, "https://x"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_load_and_filters(self):
        records, ok, n = activity_core.load(self.audit_path)
        self.assertTrue(ok)
        self.assertEqual((len(records), n), (3, 3))
        self.assertEqual(len(activity_core.apply_filter(records, "executed")), 1)
        self.assertEqual(len(activity_core.apply_filter(records, "denied")), 2)
        self.assertEqual(len(activity_core.apply_filter(records, "all")), 3)

    def test_search_and_text_table(self):
        records, _ok, _n = activity_core.load(self.audit_path)
        hits = activity_core.search(records, "network")
        self.assertEqual(len(hits), 1)
        table = activity_core.format_text_table(records)
        self.assertIn("network_send", table)
        self.assertEqual(len(table.splitlines()), 4)

    def test_malformed_log_is_reported_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "audit.jsonl")
            with open(path, "w") as fh:
                fh.write("{not valid json}\n")
            records, ok, n = activity_core.load(path)
            self.assertEqual(records, [])
            self.assertFalse(ok)
            self.assertEqual(n, 0)


if __name__ == "__main__":
    unittest.main()
