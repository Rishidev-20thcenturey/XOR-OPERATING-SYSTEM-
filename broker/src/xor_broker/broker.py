"""Broker facade: policy -> (confirmation) -> execution -> audit.

Guarantees provided here:

* Every request ends with exactly one audit record, including requests
  that raise (the record is appended from a ``finally`` block).
* Nothing executes before policy approval, and no write executes before
  user confirmation.
* Capability use counters increment only on successful execution.

Honest scope (see docs/THREAT_MODEL.md): this mediates requests made
*through this API* by cooperating tools.  It does not intercept syscalls
of arbitrary same-user processes.  System-wide enforcement via sandbox
isolation of tool/assistant processes is a later milestone.
"""

from __future__ import annotations

from typing import Optional

from . import executor
from .audit import AuditLog
from .confirm import ConfirmationManager, make_preview
from .models import (
    ActionRequest,
    Outcome,
    OutcomeStatus,
    ReasonCode,
    utcnow,
)
from .paths import canonicalize
from .policy import PolicyEngine


class Broker:
    def __init__(self, policy: PolicyEngine, confirmations: ConfirmationManager,
                 audit: AuditLog):
        self.policy = policy
        self.confirmations = confirmations
        self.audit = audit

    def handle(self, request: ActionRequest) -> Outcome:
        now = utcnow()
        decision = None
        outcome: Optional[Outcome] = None
        confirmation_label: Optional[str] = None
        try:
            decision = self.policy.evaluate(request, now)

            if not decision.allowed:
                status = (
                    OutcomeStatus.DENIED_UNSUPPORTED
                    if decision.reason is ReasonCode.UNSUPPORTED_OPERATION
                    else OutcomeStatus.DENIED_POLICY
                )
                outcome = Outcome(status, decision.reason, request.request_id,
                                  decision.capability_id)
                return outcome

            capability = self.policy.find(decision.capability_id)
            token = None
            if decision.requires_confirmation:
                digest = request.action_digest(decision.canonical_path)
                token = self.confirmations.request_approval(
                    tool=request.tool,
                    operation=request.operation.value,
                    target=decision.canonical_path,
                    description=request.description,
                    preview=make_preview(request.content),
                    action_digest=digest,
                )
                confirmation_label = "approved" if token else "denied"
                if token is None:
                    outcome = Outcome(OutcomeStatus.DENIED_USER,
                                      ReasonCode.CONFIRMATION_DENIED,
                                      request.request_id, decision.capability_id)
                    return outcome

            outcome = executor.execute(request, capability, decision.canonical_path,
                                       utcnow(), token)
            if outcome.status is OutcomeStatus.EXECUTED:
                capability.uses += 1
            return outcome

        except Exception as exc:  # fail closed, still audited below
            outcome = Outcome(OutcomeStatus.FAILED, ReasonCode.EXECUTION_ERROR,
                              request.request_id,
                              decision.capability_id if decision else None,
                              error=f"{type(exc).__name__}: {exc}")
            return outcome
        finally:
            self._audit(request, decision, outcome, confirmation_label)

    def _audit(self, request, decision, outcome, confirmation_label) -> None:
        try:
            resource = decision.canonical_path if decision and decision.canonical_path \
                else canonicalize(request.path) if request.path else "(none)"
        except Exception:
            resource = request.path or "(none)"
        self.audit.append(
            request_id=request.request_id,
            tool=request.tool,
            operation=request.operation.value,
            resource=resource,
            params_digest=request.content_digest(),
            decision=("allow" if decision and decision.allowed else "deny"),
            reason=(outcome.reason.value if outcome else "execution_error"),
            confirmation=confirmation_label,
            result=(outcome.status.value if outcome else "failed"),
            capability_id=(outcome.capability_id if outcome else None),
        )
