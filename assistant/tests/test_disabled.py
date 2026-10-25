"""The assistant must be disabled by default and have no broker bypass."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "broker", "src"))

from xor_assistant import AssistantDisabled, DisabledAssistant, declared_tools  # noqa: E402
from xor_assistant.stub import READ_ONLY_OPERATIONS  # noqa: E402
from xor_broker.models import Operation  # noqa: E402


class TestAssistantDisabled(unittest.TestCase):
    def test_disabled_by_default(self):
        assistant = DisabledAssistant()
        self.assertFalse(assistant.enabled)
        with self.assertRaises(AssistantDisabled):
            assistant.invoke_tool("read_granted_file", {"path": "/etc/passwd"})
        with self.assertRaises(AssistantDisabled):
            assistant.chat("read my files")

    def test_declared_surface_is_read_only_and_broker_backed(self):
        forbidden = {Operation.WRITE_FILE.value, Operation.DELETE_FILE.value,
                     Operation.NETWORK_SEND.value, Operation.EXEC_PROCESS.value}
        tools = declared_tools()
        self.assertTrue(tools)
        for tool in tools:
            self.assertIn(tool["broker_operation"], READ_ONLY_OPERATIONS)
            self.assertNotIn(tool["broker_operation"], forbidden)

    def test_no_direct_filesystem_or_network_apis_imported(self):
        import xor_assistant.stub as stub_module
        with open(stub_module.__file__) as fh:
            source = fh.read()
        for banned in ("import socket", "import subprocess", "urllib",
                       "os.system", "open("):
            self.assertNotIn(banned, source)


if __name__ == "__main__":
    unittest.main()
