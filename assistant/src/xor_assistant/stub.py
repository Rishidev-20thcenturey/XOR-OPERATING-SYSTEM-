"""Disabled assistant stub and its *future*, read-only tool surface.

The declared tool surface is deliberately a 1:1 map onto broker
operations, so when a model is eventually connected it will have no API
that bypasses the broker: every future tool call becomes a broker
``ActionRequest`` from tool id ``xor-assistant`` and is default-denied
unless the user has granted a matching capability.

First assistant milestone limits (enforced by tests/test_disabled.py):
read-only operations only; no write, no delete, no network, no exec.
"""

from __future__ import annotations

#: Tool identity the future assistant runner will use with the broker.
ASSISTANT_TOOL_ID = "xor-assistant"

#: The ONLY operations a future read-only assistant may ever request.
READ_ONLY_OPERATIONS = ("read_file", "list_dir")


def declared_tools():
    """Future assistant tools. Each maps to exactly one broker operation;
    none can mutate state or reach the network."""
    return [
        {"name": "read_granted_file", "broker_operation": "read_file",
         "description": "Read a file the user has explicitly granted.",
         "parameters": ["path"]},
        {"name": "list_granted_dir", "broker_operation": "list_dir",
         "description": "List a directory the user has explicitly granted.",
         "parameters": ["path"]},
    ]


class AssistantDisabled(RuntimeError):
    pass


class DisabledAssistant:
    """Placeholder where the real runner will attach (Milestone 3).

    Constructing it is harmless; *using* it always raises.  There is no
    configuration flag in the baseline that turns this into a working
    assistant - that requires new code, sandbox isolation tests and a
    passing broker corpus, by design.
    """

    enabled = False

    def __init__(self, broker_client=None):
        # Stored only to document the intended wiring: the runner's sole
        # capability channel is a BrokerClient.  It is never called here.
        self._broker_client = broker_client

    def invoke_tool(self, name: str, parameters: dict):
        raise AssistantDisabled(
            "The XOR assistant is disabled in this baseline (Milestone 3 gate). "
            "No model is integrated; see docs/ROADMAP.md.")

    def chat(self, message: str):
        raise AssistantDisabled(
            "The XOR assistant is disabled in this baseline (Milestone 3 gate).")
