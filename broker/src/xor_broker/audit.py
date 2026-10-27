"""Append-only, hash-chained audit log.

* One JSON object per line (JSONL) so the log is inspectable with plain
  tools and streamable by the activity viewer.
* Every broker request produces exactly one record - allowed, denied or
  failed - written from a ``finally`` path in the broker.
* Records contain metadata and digests only: never raw file content,
  tokens or passwords.  Request payloads appear as SHA-256 digests.
* Each record embeds ``prev_hash`` and its own ``hash`` forming a chain;
  ``verify_chain`` detects edits, deletions and reordering.  Honest
  limit: a local hash chain detects *tampering after the fact* only if
  the verifier's view of the head hash is trustworthy - it does not stop
  an attacker who controls both log and verifier (e.g. root).  See
  docs/THREAT_MODEL.md.
* File mode 0600 inside a 0700 directory.
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


class AuditIntegrityError(RuntimeError):
    """Raised when an existing audit log is not a valid, readable chain."""


def _canonical(obj: dict) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


class AuditLog:
    def __init__(self, path: str):
        self.path = path
        directory = os.path.dirname(os.path.abspath(path))
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
        if os.path.exists(self.path):
            # Verify and read the tail under a shared lock so a concurrent
            # writer cannot expose a half-written final line at startup.
            try:
                fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
            except OSError as exc:
                raise AuditIntegrityError(
                    f"audit log cannot be opened: {self.path}: {exc}") from exc
            try:
                fcntl.flock(fd, fcntl.LOCK_SH)
                try:
                    ok, checked, first_bad = verify_chain(self.path)
                    tail = self._read_tail()
                except (OSError, ValueError, UnicodeError, TypeError, KeyError) as exc:
                    raise AuditIntegrityError(
                        f"audit log is unreadable ({type(exc).__name__}): {self.path}") from exc
            finally:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
                os.close(fd)
            if not ok:
                raise AuditIntegrityError(
                    f"audit log failed integrity check at seq {first_bad} "
                    f"({checked} records): {self.path}")
            self._seq, self._last_hash = tail

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

    def _read_tail(self):
        if not os.path.exists(self.path):
            return 0, GENESIS_HASH
        last = None
        with open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    last = json.loads(line)
        if last is None:
            return 0, GENESIS_HASH
        return int(last["seq"]), str(last["hash"])

    def append(self, *, request_id: str, tool: str, operation: str, resource: str,
               params_digest: Optional[str], decision: str, reason: str,
               confirmation: Optional[str], result: str,
               capability_id: Optional[str]) -> dict:
        """Append one record under an exclusive file lock.

        The lock is important because the daemon and a one-shot
        ``xor-broker request`` may both write the same default log.  The
        tail is re-read while locked so two writers cannot fork the chain
        or reuse a sequence number.
        """
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC
        FD = os.open(self.path, flags, 0o600)
        record = None
        try:
            fcntl.flock(FD, fcntl.LOCK_EX)
            try:
                os.fchmod(FD, 0o600)
            except OSError:
                pass
            self._seq, self._last_hash = self._read_tail()
            self._seq += 1
            record = {
                "seq": self._seq,
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
                "prev_hash": self._last_hash,
            }
            record["hash"] = hashlib.sha256(
                record["prev_hash"].encode("ascii") + _canonical(record)
            ).hexdigest()
            data = (_canonical(record).decode("utf-8") + "\n").encode("utf-8")
            while data:
                written = os.write(FD, data)
                if written <= 0:
                    raise OSError("short write to audit log")
                data = data[written:]
            os.fsync(FD)
        finally:
            try:
                fcntl.flock(FD, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(FD)
        self._last_hash = record["hash"]
        return record

    def executed_counts_by_capability(self) -> dict:
        """Count successful executions per capability from the log.

        This makes ``max_uses`` durable across daemon restarts and
        one-shot CLI requests.  It deliberately trusts only records whose
        result is ``executed``; the log's chain is verified when the
        AuditLog is opened.
        """
        counts = {}
        if not os.path.exists(self.path):
            return counts
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        except OSError as exc:
            raise AuditIntegrityError(
                f"audit log cannot be opened: {self.path}: {exc}") from exc
        try:
            fcntl.flock(fd, fcntl.LOCK_SH)
            try:
                for record in read_records(self.path):
                    if not isinstance(record, dict):
                        raise AuditIntegrityError(
                            f"audit log contains a non-object record: {self.path}")
                    if record.get("result") != "executed":
                        continue
                    capability_id = record.get("capability_id")
                    if capability_id:
                        counts[capability_id] = counts.get(capability_id, 0) + 1
            except (OSError, ValueError, UnicodeError, TypeError, KeyError) as exc:
                raise AuditIntegrityError(
                    f"audit log is unreadable ({type(exc).__name__}): {self.path}") from exc
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(fd)
        return counts


def read_records(path: str) -> Iterator[dict]:
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def verify_chain(path: str) -> tuple:
    """Returns (ok: bool, records_checked: int, first_bad_seq: int|None)."""
    prev = GENESIS_HASH
    count = 0
    for record in read_records(path):
        count += 1
        if not isinstance(record, dict):
            return False, count, None
        if record.get("prev_hash") != prev:
            return False, count, record.get("seq")
        body = {k: v for k, v in record.items() if k != "hash"}
        expected = hashlib.sha256(prev.encode("ascii") + _canonical(body)).hexdigest()
        if expected != record.get("hash"):
            return False, count, record.get("seq")
        prev = record["hash"]
    return True, count, None
