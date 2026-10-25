"""Default-deny policy engine.

A request is allowed only when a capability exists that matches ALL of:
requesting tool, operation, scoped resource (exact file, or within an
explicitly granted directory), required right, not expired, not
exhausted.  Nothing is inferred from descriptions, annotations or
``untrusted_context`` (this module never reads that field).

Policy is intentionally independent of UI, audit storage and execution so
each can change without touching authorisation logic.
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable, List, Optional

from .models import (
    OPERATION_RIGHT,
    SUPPORTED_OPERATIONS,
    WRITE_OPERATIONS,
    ActionRequest,
    Capability,
    Decision,
    ReasonCode,
)
from .paths import canonicalize, scope_covers


class PolicyEngine:
    def __init__(self, capabilities: Iterable[Capability]):
        self._capabilities: List[Capability] = list(capabilities)

    @property
    def capabilities(self) -> List[Capability]:
        return list(self._capabilities)

    def add(self, capability: Capability) -> None:
        self._capabilities.append(capability)

    def find(self, capability_id: str) -> Optional[Capability]:
        for cap in self._capabilities:
            if cap.capability_id == capability_id:
                return cap
        return None

    def evaluate(self, request: ActionRequest, now: datetime) -> Decision:
        if request.operation not in SUPPORTED_OPERATIONS:
            return Decision(False, ReasonCode.UNSUPPORTED_OPERATION)

        canonical = canonicalize(request.path)
        required_right = OPERATION_RIGHT[request.operation]

        expired = exhausted = False
        for cap in self._capabilities:
            if cap.tool != request.tool:
                continue
            if cap.operation is not request.operation:
                continue
            if required_right not in cap.rights:
                continue
            if not scope_covers(cap.resource, canonical):
                continue
            if cap.is_expired(now):
                expired = True
                continue
            if cap.is_exhausted():
                exhausted = True
                continue
            return Decision(
                True,
                ReasonCode.OK,
                capability_id=cap.capability_id,
                requires_confirmation=request.operation in WRITE_OPERATIONS,
                canonical_path=canonical,
            )

        # Distinguish *why* nothing matched to make audits/debugging useful.
        if expired:
            return Decision(False, ReasonCode.CAPABILITY_EXPIRED, canonical_path=canonical)
        if exhausted:
            return Decision(False, ReasonCode.CAPABILITY_EXHAUSTED, canonical_path=canonical)
        return Decision(False, ReasonCode.NO_CAPABILITY, canonical_path=canonical)
