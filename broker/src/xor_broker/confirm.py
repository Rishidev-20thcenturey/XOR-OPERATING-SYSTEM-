"""User confirmation for write operations.

Rules enforced here and in ``executor.py``:

* Deny is the default everywhere.  EOF, Ctrl-C, provider exceptions and
  timeouts all mean *deny*.
* A confirmation is minted as an :class:`ApprovalToken` bound to the
  pending action's digest (tool + operation + canonical path + content
  digest).  It is single-use and expires after 120 s.  A token minted
  for one action cannot authorise another, and a consumed token cannot
  be replayed.
* There is intentionally no "approve always" / blanket option in the
  baseline.

Providers: :class:`CliConfirmation` (terminal; type the word ``approve``
- anything else denies; fully keyboard usable), :class:`AutoDenyConfirmation`
(non-interactive contexts), :class:`ScriptedConfirmation` (tests), and a
GTK provider in ``desktop/confirm_gtk.py`` for the desktop session
(Escape = deny, Deny button pre-focused).
"""

from __future__ import annotations

import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional, Protocol, TextIO

CONFIRMATION_TTL_SECONDS = 120


@dataclass
class ConfirmationPrompt:
    pending_id: str
    tool: str
    operation: str
    target: str
    description: str
    preview: str  # short human summary (byte count, digest, first line)

    def render(self) -> str:
        return (
            "=== XOR Broker: confirmation required ===\n"
            f"Tool requesting : {self.tool}\n"
            f"Operation       : {self.operation}\n"
            f"Target          : {self.target}\n"
            f"Change          : {self.description or '(no description provided)'}\n"
            f"Preview         : {self.preview}\n"
            "This approval applies ONLY to this one action."
        )


@dataclass
class ApprovalToken:
    pending_id: str
    action_digest: str
    expires_at: datetime
    consumed: bool = False

    def is_valid_for(self, action_digest: str, now: datetime) -> bool:
        return (
            not self.consumed
            and now < self.expires_at
            and self.action_digest == action_digest
        )


def make_preview(content: Optional[bytes]) -> str:
    """Meaningful but bounded preview; never dumps whole files to prompts."""
    if content is None:
        return "(no content)"
    import hashlib

    digest = hashlib.sha256(content).hexdigest()[:16]
    first = content[:120].decode("utf-8", errors="replace").replace("\n", "\\n")
    return f"{len(content)} bytes, sha256:{digest}..., starts: {first!r}"


class ConfirmationProvider(Protocol):
    def confirm(self, prompt: ConfirmationPrompt) -> bool: ...


class AutoDenyConfirmation:
    """Fail-closed provider for non-interactive contexts."""

    def confirm(self, prompt: ConfirmationPrompt) -> bool:
        return False


class ScriptedConfirmation:
    """Deterministic provider for tests. ``answers`` maps pending_id->bool
    or is a callable(prompt)->bool; default answer is deny."""

    def __init__(self, default: bool = False,
                 answers: Optional[Callable[[ConfirmationPrompt], bool]] = None):
        self.default = default
        self.answers = answers
        self.seen: list = []

    def confirm(self, prompt: ConfirmationPrompt) -> bool:
        self.seen.append(prompt)
        if self.answers is not None:
            return bool(self.answers(prompt))
        return self.default


class CliConfirmation:
    """Terminal confirmation.  Default deny: only the exact word
    ``approve`` (case-insensitive) approves.  EOF / Ctrl-C deny."""

    def __init__(self, instream: Optional[TextIO] = None,
                 outstream: Optional[TextIO] = None):
        self.instream = instream if instream is not None else sys.stdin
        self.outstream = outstream if outstream is not None else sys.stdout

    def confirm(self, prompt: ConfirmationPrompt) -> bool:
        try:
            self.outstream.write(prompt.render() + "\n")
            self.outstream.write("Type 'approve' to allow this ONE action, anything else denies: ")
            self.outstream.flush()
            answer = self.instream.readline()
        except (EOFError, KeyboardInterrupt, OSError):
            return False
        if not answer:  # EOF
            return False
        return answer.strip().lower() == "approve"


class ConfirmationManager:
    """Mints and validates single-use, digest-bound approval tokens."""

    def __init__(self, provider: ConfirmationProvider):
        self.provider = provider

    def request_approval(self, *, tool: str, operation: str, target: str,
                         description: str, preview: str,
                         action_digest: str) -> Optional[ApprovalToken]:
        prompt = ConfirmationPrompt(
            pending_id=uuid.uuid4().hex,
            tool=tool, operation=operation, target=target,
            description=description, preview=preview,
        )
        try:
            approved = self.provider.confirm(prompt)
        except Exception:
            return None  # any provider failure denies (fail closed)
        if not approved:
            return None
        return ApprovalToken(
            pending_id=prompt.pending_id,
            action_digest=action_digest,
            expires_at=datetime.now(timezone.utc) + timedelta(
                seconds=CONFIRMATION_TTL_SECONDS),
        )
