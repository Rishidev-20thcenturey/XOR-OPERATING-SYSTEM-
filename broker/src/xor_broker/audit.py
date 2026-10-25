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

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Iterator, Optional

GENESIS_HASH = "0" * 64


def _canonical(obj: dict) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


class AuditLog:
    def __init__(self, path: str):
        self.path = path
        directory = os.path.dirname(os.path.abspath(path))
        os.makedirs(directory, mode=0o700, exist_ok=True)
        try:
            os.chmod(directory, 0o700)
        except OSError:
            pass
        self._seq, self._last_hash = self._read_tail()

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
        line = _canonical(record).decode("utf-8") + "\n"
        FD = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(FD, line.encode("utf-8"))
            os.fsync(FD)
        finally:
            os.close(FD)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        self._last_hash = record["hash"]
        return record


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
        if record.get("prev_hash") != prev:
            return False, count, record.get("seq")
        body = {k: v for k, v in record.items() if k != "hash"}
        expected = hashlib.sha256(prev.encode("ascii") + _canonical(body)).hexdigest()
        if expected != record.get("hash"):
            return False, count, record.get("seq")
        prev = record["hash"]
    return True, count, None
