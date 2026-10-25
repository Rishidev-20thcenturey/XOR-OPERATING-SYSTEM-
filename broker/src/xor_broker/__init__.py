"""XOR OS permission broker (baseline).

Default-deny authorisation boundary for tool/assistant-issued actions.
Standard library only - runs on any Python >= 3.9.
"""

__version__ = "0.1.0"

from .broker import Broker
from .models import ActionRequest, Capability, Operation, Outcome

__all__ = ["Broker", "ActionRequest", "Capability", "Operation", "Outcome", "__version__"]
