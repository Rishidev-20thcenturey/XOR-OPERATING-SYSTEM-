"""Core typed data model for the XOR permission broker.

Design notes
------------
* Every broker interaction is an :class:`ActionRequest` naming a tool, an
  :class:`Operation` and a resource.  There is deliberately no "shell"
  operation and no generic "grant everything" capability.
* Operations the baseline cannot enforce safely (delete, network send,
  process execution) exist in the type system so requests for them can be
  *denied explicitly and audited* instead of being silently impossible to
  express.  They are never grantable in a manifest (see ``manifest.py``).
* ``untrusted_context`` on a request carries text that originated from
  documents/tool output.  The policy engine never reads it.  It exists so
  tests can prove prompt-injection style text cannot influence decisions.
"""

from __future__ import annotations

import enum
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


class Operation(str, enum.Enum):
    READ_FILE = "read_file"
    WRITE_FILE = "write_file"
    LIST_DIR = "list_dir"
    DELETE_FILE = "delete_file"      # not grantable in baseline -> always denied
    NETWORK_SEND = "network_send"    # not grantable in baseline -> always denied
    EXEC_PROCESS = "exec_process"    # not grantable in baseline -> always denied


#: Operations the baseline can actually enforce and execute.
SUPPORTED_OPERATIONS = frozenset(
    {Operation.READ_FILE, Operation.WRITE_FILE, Operation.LIST_DIR}
)

#: Operations that mutate state and therefore always need user confirmation.
WRITE_OPERATIONS = frozenset({Operation.WRITE_FILE})

#: Right required per operation.  Capabilities carry rights; a capability
#: must name the operation *and* hold the matching right.
OPERATION_RIGHT = {
    Operation.READ_FILE: "read",
    Operation.LIST_DIR: "read",
    Operation.WRITE_FILE: "write",
}

VALID_RIGHTS = frozenset({"read", "write"})


class ScopeKind(str, enum.Enum):
    FILE = "file"   # exactly one file
    DIR = "dir"     # a directory subtree, only when explicitly granted


class OutcomeStatus(str, enum.Enum):
    EXECUTED = "executed"
    DENIED_POLICY = "denied_policy"
    DENIED_USER = "denied_user"
    DENIED_UNSUPPORTED = "denied_unsupported"
    FAILED = "failed"


class ReasonCode(str, enum.Enum):
    OK = "ok"
    NO_CAPABILITY = "no_capability"
    CAPABILITY_EXPIRED = "capability_expired"
    CAPABILITY_EXHAUSTED = "capability_exhausted"
    UNSUPPORTED_OPERATION = "unsupported_operation"
    CONFIRMATION_DENIED = "confirmation_denied"
    CONFIRMATION_FAILED = "confirmation_failed"
    STALE_CONFIRMATION = "stale_confirmation"
    DIGEST_MISMATCH = "digest_mismatch"
    PATH_ESCAPE = "path_escape"
    EXECUTION_ERROR = "execution_error"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class ResourceScope:
    """A narrowly scoped resource set. ``path`` is canonical (realpath)."""

    kind: ScopeKind
    path: str


@dataclass
class Capability:
    """An explicit grant: one tool, one operation, one scoped resource."""

    capability_id: str
    tool: str
    operation: Operation
    resource: ResourceScope
    rights: frozenset
    expires_at: Optional[datetime] = None
    max_uses: Optional[int] = None
    uses: int = 0
    # Optional (st_dev, st_ino) pin captured when a *file* grant is issued
    # through a file-selection flow.  When set, the executor verifies the
    # opened file is the same inode, defeating replacement/hardlink tricks
    # for that grant.  Manifests normally leave this unset (null).
    pin_dev: Optional[int] = None
    pin_ino: Optional[int] = None

    def is_expired(self, now: datetime) -> bool:
        return self.expires_at is not None and now >= self.expires_at

    def is_exhausted(self) -> bool:
        return self.max_uses is not None and self.uses >= self.max_uses


@dataclass
class ActionRequest:
    tool: str
    operation: Operation
    path: str
    content: Optional[bytes] = None          # payload for WRITE_FILE
    description: str = ""                      # human summary shown to user
    untrusted_context: Optional[str] = None    # NEVER consulted by policy
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def content_digest(self) -> Optional[str]:
        if self.content is None:
            return None
        return hashlib.sha256(self.content).hexdigest()

    def action_digest(self, canonical_path: str) -> str:
        """Digest binding a confirmation to *this exact* action."""
        payload = {
            "tool": self.tool,
            "operation": self.operation.value,
            "path": canonical_path,
            "content_sha256": self.content_digest(),
        }
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: ReasonCode
    capability_id: Optional[str] = None
    requires_confirmation: bool = False
    canonical_path: Optional[str] = None


@dataclass
class Outcome:
    status: OutcomeStatus
    reason: ReasonCode
    request_id: str
    capability_id: Optional[str] = None
    data: Optional[bytes] = None        # READ_FILE payload (never logged)
    listing: Optional[list] = None       # LIST_DIR payload (names only)
    error: Optional[str] = None
