"""Append-only, hash-chained audit log.

* One JSON object per line (JSONL) so the log is inspectable with plain
  tools and streamable by the activity viewer.
* Every broker request produces exactly one record - allowed, denied or
  failed - written from a ``finally`` path in the broker.  If that append
  fails, the broker reports an explicit ``audit_failure`` outcome instead
  of pretending the request was recorded.  This is not crash-atomic: a
  crash between execution and append can leave an execution uncounted.
* Records contain metadata and digests only: never raw file content,
  tokens or passwords.  Request payloads appear as SHA-256 digests.
* Each record embeds ``prev_hash`` and its own ``hash`` forming a chain;
  ``verify_chain`` detects edits, deletions and reordering.  Honest
  limit: a local hash chain detects *tampering after the fact* only if
  the verifier's view of the head hash is trustworthy - it does not stop
  an attacker who controls both log and verifier (e.g. root).  See
  docs/THREAT_MODEL.md.
* File mode 0600 inside a 0700 directory.
* Reads, chain verification, tail discovery and appends are anchored to
  one opened file descriptor under a shared/exclusive ``flock``.  The
  pathname is checked against that descriptor while locked, so replacing
  the log path makes the operation fail closed instead of silently using
  a new file.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator, Optional

GENESIS_HASH = "0" * 64


class AuditError(RuntimeError):
    """Base class for audit failures reported to broker callers."""


class AuditIntegrityError(AuditError):
    """Raised when an existing audit log is not a valid, readable chain."""


class AuditWriteError(AuditError):
    """Raised when a record cannot be durably appended to the audit log."""


def _canonical(obj: dict) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _identity(st: os.stat_result) -> tuple:
    return st.st_dev, st.st_ino


def _iter_lines_fd(fd: int) -> Iterator[str]:
    """Yield non-empty lines from an already-locked descriptor.

    ``dup`` shares the file offset with ``fd`` but has its own close
    handle, so the caller's descriptor remains usable after iteration.
    """
    os.lseek(fd, 0, os.SEEK_SET)
    with os.fdopen(os.dup(fd), "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield line


def _check_record(record: object, expected_seq: int, prev: str) -> tuple:
    """Validate one record structurally and cryptographically.

    Returns ``(ok, seq_or_none, reason)``.  Sequence numbers must be
    contiguous and start at 1; that is stronger than checking the hash
    chain alone and prevents a syntactically valid but reordered or
    truncated-into-the-middle record set from being counted.
    """
    if not isinstance(record, dict):
        return False, None, "record is not a JSON object"
    seq = record.get("seq")
    if type(seq) is not int or seq != expected_seq:
        return False, seq if type(seq) is int else None, "sequence number is missing or out of order"
    if record.get("prev_hash") != prev:
        return False, seq, "prev_hash does not match the previous record"
    for key in ("hash", "result", "capability_id"):
        if key not in record:
            return False, seq, f"required field {key!r} is missing"
    digest = record.get("hash")
    if not isinstance(digest, str) or len(digest) != 64:
        return False, seq, "hash is not a SHA-256 hex digest"
    if not isinstance(record.get("result"), str):
        return False, seq, "result must be a string"
    capability_id = record.get("capability_id")
    if capability_id is not None and not isinstance(capability_id, str):
        return False, seq, "capability_id must be a string or null"
    if record["result"] == "executed" and not capability_id:
        return False, seq, "executed record lacks a capability_id"
    body = {k: v for k, v in record.items() if k != "hash"}
    try:
        expected = hashlib.sha256(prev.encode("ascii") + _canonical(body)).hexdigest()
    except (TypeError, ValueError) as exc:
        return False, seq, f"record cannot be hashed: {exc}"
    if expected != digest:
        return False, seq, "hash does not match the record contents"
    return True, seq, ""


def _scan_fd(fd: int) -> tuple:
    """Verify a chain and count executed uses from one locked descriptor.

    Returns ``(counts, records_checked, last_hash, first_bad_seq, error)``.
    On any malformed or hash-invalid record ``counts`` must be discarded.
    """
    counts = {}
    prev = GENESIS_HASH
    count = 0
    for line in _iter_lines_fd(fd):
        count += 1
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, UnicodeError) as exc:
            return counts, count, None, None, f"record {count} is not valid JSON: {exc}"
        ok, bad_seq, reason = _check_record(record, count, prev)
        if not ok:
            return counts, count, None, bad_seq, reason
        if record["result"] == "executed":
            capability_id = record["capability_id"]
            if capability_id:
                counts[capability_id] = counts.get(capability_id, 0) + 1
        prev = record["hash"]
    return counts, count, prev, None, None


class AuditLog:
    def __init__(self, path: str):
        # Canonicalise the parent (so aliases share one use-lock) without
        # resolving the final component: a symlinked audit log must still
        # be rejected by O_NOFOLLOW rather than silently followed here.
        absolute = os.path.abspath(path)
        self.path = os.path.join(
            os.path.realpath(os.path.dirname(absolute)), os.path.basename(absolute))
        directory = os.path.dirname(self.path)
        existed = os.path.isdir(directory)
        os.makedirs(directory, mode=0o700, exist_ok=True)
        if not existed:
            # Only harden a directory this process created.  Chmodding an
            # existing arbitrary parent (e.g. /tmp for a custom --audit)
            # would be a surprising, system-wide side effect.
            try:
                os.chmod(directory, 0o700)
            except OSError:
                pass
        self._seq, self._last_hash = 0, GENESIS_HASH
        self._identity = None
        if os.path.exists(self.path):
            # Verify and read the tail under a shared lock so a concurrent
            # writer cannot expose a half-written final line at startup.
            fd = self._open_audit_fd(os.O_RDONLY, AuditIntegrityError)
            try:
                try:
                    fcntl.flock(fd, fcntl.LOCK_SH)
                except OSError as exc:
                    raise AuditIntegrityError(
                        f"audit log cannot be locked: {self.path}: {exc}") from exc
                try:
                    self._assert_path_matches_fd(fd)
                    try:
                        _counts, checked, last_hash, first_bad, why = _scan_fd(fd)
                    except (OSError, UnicodeError) as exc:
                        raise AuditIntegrityError(
                            f"audit log is unreadable ({type(exc).__name__}): "
                            f"{self.path}") from exc
                    st = os.fstat(fd)
                finally:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_UN)
                    except OSError:
                        pass
            finally:
                os.close(fd)
            if first_bad is not None or why:
                raise AuditIntegrityError(
                    f"audit log failed integrity check at seq {first_bad} "
                    f"({checked} records): {why}: {self.path}")
            self._seq, self._last_hash = checked, last_hash
            self._identity = _identity(st)

    def _open_audit_fd(self, flags: int, error_cls) -> int:
        """Open the audit file with no-follow, regular-file enforcement."""
        try:
            fd = os.open(
                self.path,
                flags | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                0o600,
            )
        except OSError as exc:
            raise error_cls(f"audit log cannot be opened: {self.path}: {exc}") from exc
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                raise error_cls(f"audit log is not a regular file: {self.path}")
        except BaseException:
            os.close(fd)
            raise
        return fd

    def _assert_path_matches_fd(self, fd: int) -> None:
        """Fail if the pathname was replaced since the descriptor opened."""
        st = os.fstat(fd)
        try:
            path_st = os.stat(self.path, follow_symlinks=False)
        except OSError as exc:
            raise AuditIntegrityError(
                f"audit log path is no longer available: {self.path}: {exc}") from exc
        ident = _identity(st)
        if ident != _identity(path_st):
            raise AuditIntegrityError(
                f"audit log path no longer names the opened file: {self.path}")
        if self._identity is not None and ident != self._identity:
            raise AuditIntegrityError(
                f"audit log file was replaced after it was opened: {self.path}")

    @contextmanager
    def execution_lock(self):
        """Serialize capability use across all broker processes.

        ``max_uses`` is derived from executed audit records, so checking
        and consuming a use must not race another daemon/CLI instance.
        The lock is held across policy evaluation, execution and the
        audit append that commits the use.  It is a sibling of the audit
        log (not the log itself) so ``append`` can still take its own
        exclusive lock without deadlocking.
        """
        lock_path = self.path + ".use.lock"
        try:
            fd = os.open(
                lock_path,
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
            )
        except OSError as exc:
            raise AuditIntegrityError(
                f"capability-use lock cannot be opened: {lock_path}: {exc}") from exc
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
                raise AuditIntegrityError(
                    f"capability-use lock is not a private regular file: {lock_path}")
            try:
                os.fchmod(fd, 0o600)
            except OSError as exc:
                raise AuditIntegrityError(
                    f"capability-use lock cannot be secured: {lock_path}: {exc}") from exc
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
            except OSError as exc:
                raise AuditIntegrityError(
                    f"capability-use lock cannot be acquired: {lock_path}: {exc}") from exc
            try:
                yield
            finally:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
        finally:
            os.close(fd)

    def append(self, *, request_id: str, tool: str, operation: str, resource: str,
               params_digest: Optional[str], decision: str, reason: str,
               confirmation: Optional[str], result: str,
               capability_id: Optional[str]) -> dict:
        """Append one record under an exclusive file lock.

        The locked descriptor is also used to re-read and re-verify the
        tail, so two writers cannot fork the chain or reuse a sequence
        number.  A write/sync failure raises :class:`AuditWriteError`
        after the action may already have run; the broker converts that
        to an explicit failure outcome.  This is not crash-atomic.
        """
        if self._identity is not None and not os.path.exists(self.path):
            raise AuditIntegrityError(
                f"audit log disappeared after it was opened: {self.path}")
        flags = os.O_RDWR | os.O_APPEND | os.O_CREAT
        fd = self._open_audit_fd(flags, AuditWriteError)
        record = None
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
            except OSError as exc:
                raise AuditWriteError(
                    f"audit log cannot be locked for append: {self.path}: {exc}") from exc
            try:
                try:
                    os.fchmod(fd, 0o600)
                except OSError:
                    pass
                self._assert_path_matches_fd(fd)
                try:
                    _counts, checked, last_hash, first_bad, why = _scan_fd(fd)
                except (OSError, UnicodeError) as exc:
                    raise AuditIntegrityError(
                        f"audit log is unreadable ({type(exc).__name__}): "
                        f"{self.path}") from exc
                if first_bad is not None or why:
                    raise AuditIntegrityError(
                        f"refusing to append to an invalid audit chain at seq "
                        f"{first_bad} ({checked} records): {why}: {self.path}")
                if checked < self._seq:
                    raise AuditIntegrityError(
                        f"audit log was truncated from seq {self._seq} to "
                        f"{checked}: {self.path}")
                st = os.fstat(fd)
                seq = checked + 1
                record = {
                    "seq": seq,
                    "event_id": uuid.uuid4().hex,
                    "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "request_id": request_id,
                    "tool": tool,
                    "operation": operation,
                    "resource": resource,
                    "params_digest": params_digest,
                    "decision": decision,
                    "reason": reason,
                    "confirmation": confirmation,
                    "result": result,
                    "capability_id": capability_id,
                    "prev_hash": last_hash,
                }
                record["hash"] = hashlib.sha256(
                    record["prev_hash"].encode("ascii") + _canonical(record)
                ).hexdigest()
                data = (_canonical(record).decode("utf-8") + "\n").encode("utf-8")
                try:
                    while data:
                        written = os.write(fd, data)
                        if written <= 0:
                            raise OSError("short write to audit log")
                        data = data[written:]
                    os.fsync(fd)
                    # Re-check after the write as well: an attacker could
                    # have swapped the pathname while the record was being
                    # written.  The record is then not reachable at the
                    # audited path, so report failure even if bytes landed
                    # on the old inode.
                    self._assert_path_matches_fd(fd)
                except OSError as exc:
                    raise AuditWriteError(
                        f"audit record could not be written: {self.path}: {exc}") from exc
                self._seq = seq
                self._last_hash = record["hash"]
                self._identity = _identity(st)
            finally:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
        finally:
            os.close(fd)
        return record

    def executed_counts_by_capability(self) -> dict:
        """Count successful executions per capability from one locked FD.

        The chain is verified in the same pass that builds the counts.
        Malformed, non-contiguous or hash-invalid records raise
        :class:`AuditIntegrityError` instead of being ignored, so a
        damaged log cannot silently reset ``max_uses``.  Replacing the
        audit pathname after startup also fails closed.
        """
        if not os.path.exists(self.path):
            if self._identity is not None:
                raise AuditIntegrityError(
                    f"audit log disappeared after it was opened: {self.path}")
            return {}
        fd = self._open_audit_fd(os.O_RDONLY, AuditIntegrityError)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_SH)
            except OSError as exc:
                raise AuditIntegrityError(
                    f"audit log cannot be locked: {self.path}: {exc}") from exc
            try:
                self._assert_path_matches_fd(fd)
                try:
                    counts, checked, last_hash, first_bad, why = _scan_fd(fd)
                except (OSError, UnicodeError) as exc:
                    raise AuditIntegrityError(
                        f"audit log is unreadable ({type(exc).__name__}): "
                        f"{self.path}") from exc
                st = os.fstat(fd)
            finally:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
        finally:
            os.close(fd)
        if first_bad is not None or why:
            raise AuditIntegrityError(
                f"audit log failed integrity check at seq {first_bad} "
                f"({checked} records): {why}: {self.path}")
        if checked < self._seq:
            raise AuditIntegrityError(
                f"audit log was truncated from seq {self._seq} to "
                f"{checked}: {self.path}")
        self._seq, self._last_hash = checked, last_hash
        self._identity = _identity(st)
        return counts


def read_records(path: str) -> Iterator[dict]:
    """Yield records for inspection, using one locked no-follow FD."""
    if not os.path.exists(path):
        return
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    except OSError as exc:
        raise AuditIntegrityError(f"audit log cannot be opened: {path}: {exc}") from exc
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise AuditIntegrityError(f"audit log is not a regular file: {path}")
        try:
            fcntl.flock(fd, fcntl.LOCK_SH)
        except OSError as exc:
            raise AuditIntegrityError(
                f"audit log cannot be locked: {path}: {exc}") from exc
        try:
            for line in _iter_lines_fd(fd):
                yield json.loads(line)
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
    finally:
        os.close(fd)


def verify_chain(path: str) -> tuple:
    """Returns (ok: bool, records_checked: int, first_bad_seq: int|None)."""
    if not os.path.exists(path):
        return True, 0, None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    except OSError as exc:
        raise AuditIntegrityError(f"audit log cannot be opened: {path}: {exc}") from exc
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise AuditIntegrityError(f"audit log is not a regular file: {path}")
        try:
            fcntl.flock(fd, fcntl.LOCK_SH)
        except OSError as exc:
            raise AuditIntegrityError(
                f"audit log cannot be locked: {path}: {exc}") from exc
        try:
            try:
                _counts, count, _last_hash, first_bad, why = _scan_fd(fd)
            except (OSError, UnicodeError):
                return False, 0, None
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
    finally:
        os.close(fd)
    if first_bad is not None or why:
        return False, count, first_bad
    return True, count, None
