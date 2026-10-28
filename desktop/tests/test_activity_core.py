"""Activity viewer core: filtering, search, chain status, text output."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "broker", "src"))

import activity_core  # noqa: E402
import xor_activity  # noqa: E402
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


    def test_filter_toggle_sync_does_not_recurse_and_keeps_one_selected(self):
        state = {"filter": "all", "query": "", "refreshing": False}
        refresh_calls = []

        class Toggle:
            def __init__(self):
                self.active = False
                self.callback = None

            def connect(self, signal, callback):
                assert signal == "toggled"
                self.callback = callback

            def get_active(self):
                return self.active

            def set_active(self, active):
                if active == self.active:
                    return
                self.active = active
                if self.callback is not None:
                    self.callback(self)

        buttons = {name: Toggle()
                   for name in ("all", "executed", "denied", "failed")}

        def refresh():
            refresh_calls.append(state["filter"])
            xor_activity._sync_filter_buttons(buttons, state["filter"], state)

        for name, button in buttons.items():
            button.connect(
                "toggled",
                lambda btn, selected=name: xor_activity._on_filter_toggled(
                    selected, btn, state, refresh),
            )

        xor_activity._sync_filter_buttons(buttons, state["filter"], state)
        self.assertTrue(buttons["all"].get_active())
        self.assertEqual(sum(b.get_active() for b in buttons.values()), 1)

        # Simulate a user selecting another filter.
        buttons["executed"].set_active(True)
        self.assertEqual(state["filter"], "executed")
        self.assertTrue(buttons["executed"].get_active())
        self.assertEqual(sum(b.get_active() for b in buttons.values()), 1)

        # Turning the selected toggle off must restore it, not recurse.
        buttons["executed"].set_active(False)
        self.assertEqual(state["filter"], "executed")
        self.assertTrue(buttons["executed"].get_active())
        self.assertEqual(sum(b.get_active() for b in buttons.values()), 1)
        self.assertFalse(state["refreshing"])
        self.assertEqual(refresh_calls, ["executed", "executed"])

if __name__ == "__main__":
    unittest.main()
