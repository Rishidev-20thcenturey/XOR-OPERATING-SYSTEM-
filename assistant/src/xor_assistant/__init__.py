"""XOR assistant integration point — DISABLED STUB (Milestone 3 gate).

No model is bundled, downloaded or called.  No network access.  The
assistant is off by default and cannot be enabled in the baseline: per
the project plan it may only be wired up after (a) the broker security
corpus passes and (b) the assistant runner is isolated so the broker is
its only route to user resources.  See docs/ROADMAP.md Milestone 3.
"""

__version__ = "0.1.0"

from .stub import AssistantDisabled, DisabledAssistant, declared_tools

__all__ = ["AssistantDisabled", "DisabledAssistant", "declared_tools", "__version__"]
