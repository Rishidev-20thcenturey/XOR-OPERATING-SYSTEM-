"""Execution of authorised actions - the only place that touches files.

The executor re-verifies everything safety-critical instead of trusting
upstream checks (defence in depth):

* For writes it requires a valid, unconsumed :class:`ApprovalToken`
  whose digest matches the *actual* request being executed.
* Files are opened through ``paths.open_scoped_*`` (FD-anchored,
  ``O_NOFOLLOW``), so approval-time validation cannot be raced.
* Inode pins from file/directory-selection grants are checked with
  ``fstat`` before any content is read, listed, written or truncated.
  Write opens omit ``O_TRUNC`` so a replaced pinned file is rejected
  without destroying the replacement.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Optional

from .confirm import ApprovalToken
from .models import ActionRequest, Capability, Operation, Outcome, OutcomeStatus, ReasonCode
from .paths import PathEscapeError, open_scoped_dir, open_scoped_file

MAX_READ_BYTES = 16 * 1024 * 1024  # broker reads are bounded; no huge blobs


def execute(request: ActionRequest, capability: Capability, canonical_path: str,
            now: datetime, token: Optional[ApprovalToken] = None) -> Outcome:
    op = request.operation

    if op is Operation.WRITE_FILE:
        digest = request.action_digest(canonical_path)
        if token is None:
            return Outcome(OutcomeStatus.DENIED_USER, ReasonCode.CONFIRMATION_DENIED,
                           request.request_id, capability.capability_id)
        if token.consumed or now >= token.expires_at:
            return Outcome(OutcomeStatus.DENIED_POLICY, ReasonCode.STALE_CONFIRMATION,
                           request.request_id, capability.capability_id)
        if token.action_digest != digest:
            return Outcome(OutcomeStatus.DENIED_POLICY, ReasonCode.DIGEST_MISMATCH,
                           request.request_id, capability.capability_id)
        token.consumed = True  # single use, consumed *before* acting

    try:
        if op is Operation.READ_FILE:
            FD = open_scoped_file(capability.resource, canonical_path, write=False)
            try:
                _check_pin(FD, capability)
                data = os.read(FD, MAX_READ_BYTES + 1)
            finally:
                os.close(FD)
            if len(data) > MAX_READ_BYTES:
                return _fail(request, capability, "file exceeds broker read limit")
            return Outcome(OutcomeStatus.EXECUTED, ReasonCode.OK,
                           request.request_id, capability.capability_id, data=data)

        if op is Operation.WRITE_FILE:
            if request.content is None:
                return _fail(request, capability, "write request without content")
            # A pinned grant must refer to an existing inode.  Do not create
            # a missing path, and do not truncate until the opened inode has
            # been checked against the pin.
            FD = open_scoped_file(capability.resource, canonical_path, write=True,
                                  create=capability.pin_ino is None)
            try:
                _check_pin(FD, capability)
                os.ftruncate(FD, 0)
                view = memoryview(request.content)
                while view:
                    written = os.write(FD, view)
                    view = view[written:]
                os.fsync(FD)
            finally:
                os.close(FD)
            return Outcome(OutcomeStatus.EXECUTED, ReasonCode.OK,
                           request.request_id, capability.capability_id)

        if op is Operation.LIST_DIR:
            FD = open_scoped_dir(capability.resource, canonical_path)
            try:
                _check_pin(FD, capability)
                names = sorted(os.listdir(FD))
            finally:
                os.close(FD)
            return Outcome(OutcomeStatus.EXECUTED, ReasonCode.OK,
                           request.request_id, capability.capability_id, listing=names)

    except PathEscapeError as exc:
        return Outcome(OutcomeStatus.DENIED_POLICY, ReasonCode.PATH_ESCAPE,
                       request.request_id, capability.capability_id, error=str(exc))
    except OSError as exc:
        return _fail(request, capability, f"{type(exc).__name__}: {exc.strerror or exc}")

    return Outcome(OutcomeStatus.DENIED_UNSUPPORTED, ReasonCode.UNSUPPORTED_OPERATION,
                   request.request_id, capability.capability_id)


def _check_pin(FD: int, capability: Capability) -> None:
    if capability.pin_ino is None:
        return
    st = os.fstat(FD)
    if (st.st_dev, st.st_ino) != (capability.pin_dev, capability.pin_ino):
        # A pinned grant whose file was replaced must fail closed.
        raise PathEscapeError("opened file does not match pinned inode of grant")


def _fail(request, capability, message: str) -> Outcome:
    return Outcome(OutcomeStatus.FAILED, ReasonCode.EXECUTION_ERROR,
                   request.request_id, capability.capability_id, error=message)
