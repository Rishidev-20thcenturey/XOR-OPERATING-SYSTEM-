"""Broker facade: policy -> (confirmation) -> execution -> audit.

Guarantees provided here:

* Every request attempts exactly one audit record, including requests
  that raise (the record is appended from a ``finally`` block).  If the
  append itself fails, the caller receives an explicit ``audit_failure``
  outcome - the broker never returns ``executed`` when it could not
  record the execution.
* Nothing executes before policy approval, and no write executes before
  user confirmation.
* Capability use counters increment only on successful execution and
  are re-derived from the audit log under a cross-process use lock, so
  ``max_uses`` is not exceeded by concurrent daemon/CLI instances.

Honest scope (see docs/THREAT_MODEL.md): this mediates requests made
*through this API* by cooperating tools.  It does not intercept syscalls
of arbitrary same-user processes.  System-wide enforcement via sandbox
isolation of tool/assistant processes is a later milestone.  Execution
and audit append are not crash-atomic: a process/kernel crash after the
action but before the durable append can undercount ``max_uses``.
"""

from __future__ import annotations

from typing import Optional

from . import executor
from .audit import AuditIntegrityError, AuditLog
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
        decision = None
        try:
            with self.audit.execution_lock():
                self._refresh_uses()
                decision = self.policy.evaluate(request, utcnow())
                if not decision.allowed:
                    outcome = self._decision_denial(request, decision)
                    return self._audit_or_failure(request, decision, outcome, None)
                if not decision.requires_confirmation:
                    return self._execute_and_audit_locked(
                        request, decision, None, None)
        except AuditIntegrityError as exc:
            # The use lock or audit ledger could not be trusted.  Fail
            # closed, and still attempt a denial record in the trail.
            outcome = Outcome(OutcomeStatus.FAILED, ReasonCode.EXECUTION_ERROR,
                              request.request_id,
                              error=f"{type(exc).__name__}: {exc}")
            return self._audit_or_failure(request, None, outcome, None)
        except Exception as exc:
            outcome = Outcome(OutcomeStatus.FAILED, ReasonCode.EXECUTION_ERROR,
                              request.request_id,
                              decision.capability_id if decision else None,
                              error=f"{type(exc).__name__}: {exc}")
            return self._audit_or_failure(request, decision, outcome, None)

        # Only an allowed request requiring confirmation reaches this point.
        # Crucially, the cross-process use lock is not held while the user
        # decides.  Concurrent reads and unrelated actions can proceed.
        token = self.confirmations.request_approval(
            tool=request.tool,
            operation=request.operation.value,
            target=decision.canonical_path,
            description=request.description,
            preview=make_preview(request.content),
            action_digest=request.action_digest(decision.canonical_path),
        )
        confirmation_label = "approved" if token else "denied"

        decision = None
        try:
            with self.audit.execution_lock():
                # Another request may have consumed this capability while the
                # prompt was open.  Refresh and re-evaluate before execution.
                self._refresh_uses()
                decision = self.policy.evaluate(request, utcnow())
                if not decision.allowed:
                    outcome = self._decision_denial(request, decision)
                    return self._audit_or_failure(
                        request, decision, outcome, confirmation_label)

                # Approval is valid only while the same policy still requires
                # it and the token remains bound to this exact action.
                if not decision.requires_confirmation:
                    outcome = Outcome(
                        OutcomeStatus.DENIED_POLICY,
                        ReasonCode.CONFIRMATION_DENIED,
                        request.request_id,
                        decision.capability_id,
                    )
                    return self._audit_or_failure(
                        request, decision, outcome, confirmation_label)
                if token is None or not token.is_valid_for(
                        request.action_digest(decision.canonical_path), utcnow()):
                    outcome = Outcome(
                        OutcomeStatus.DENIED_USER,
                        ReasonCode.CONFIRMATION_DENIED,
                        request.request_id,
                        decision.capability_id,
                    )
                    return self._audit_or_failure(
                        request, decision, outcome, confirmation_label)

                return self._execute_and_audit_locked(
                    request, decision, token, confirmation_label)
        except AuditIntegrityError as exc:
            outcome = Outcome(OutcomeStatus.FAILED, ReasonCode.EXECUTION_ERROR,
                              request.request_id,
                              error=f"{type(exc).__name__}: {exc}")
            return self._audit_or_failure(
                request, decision, outcome, confirmation_label)
        except Exception as exc:
            outcome = Outcome(OutcomeStatus.FAILED, ReasonCode.EXECUTION_ERROR,
                              request.request_id,
                              decision.capability_id if decision else None,
                              error=f"{type(exc).__name__}: {exc}")
            return self._audit_or_failure(
                request, decision, outcome, confirmation_label)

    def _refresh_uses(self) -> None:
        """Reload durable execution counts while holding the use lock."""
        counts = self.audit.executed_counts_by_capability()
        for capability in self.policy.capabilities:
            capability.uses = counts.get(capability.capability_id, 0)

    @staticmethod
    def _decision_denial(request: ActionRequest, decision) -> Outcome:
        status = (
            OutcomeStatus.DENIED_UNSUPPORTED
            if decision.reason is ReasonCode.UNSUPPORTED_OPERATION
            else OutcomeStatus.DENIED_POLICY
        )
        return Outcome(status, decision.reason, request.request_id,
                       decision.capability_id)

    def _execute_and_audit_locked(self, request, decision, token,
                                  confirmation_label) -> Outcome:
        try:
            capability = self.policy.find(decision.capability_id)
            outcome = executor.execute(
                request, capability, decision.canonical_path, utcnow(), token)
            if outcome.status is OutcomeStatus.EXECUTED:
                capability.uses += 1
        except Exception as exc:
            outcome = Outcome(
                OutcomeStatus.FAILED, ReasonCode.EXECUTION_ERROR,
                request.request_id, decision.capability_id,
                error=f"{type(exc).__name__}: {exc}",
            )
        return self._audit_or_failure(
            request, decision, outcome, confirmation_label)

    def _audit_or_failure(self, request, decision, outcome, confirmation_label) -> Outcome:
        try:
            self._audit(request, decision, outcome, confirmation_label)
        except Exception as exc:
            return self._audit_failure(request, outcome, exc)
        return outcome

    def _audit_failure(self, request, prior_outcome, error) -> Outcome:
        executed = prior_outcome is not None and \
            prior_outcome.status is OutcomeStatus.EXECUTED
        capability_id = prior_outcome.capability_id if prior_outcome else None
        if executed:
            detail = (
                "action executed but the audit record could not be written; "
                "the execution may be missing from max_uses accounting and "
                "the caller receives no result data"
            )
        else:
            detail = (
                "audit record could not be written; the request outcome is "
                "not durably recorded"
            )
        return Outcome(
            OutcomeStatus.FAILED,
            ReasonCode.AUDIT_FAILURE,
            request.request_id,
            capability_id,
            error=f"{detail} ({type(error).__name__}: {error})",
        )

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
