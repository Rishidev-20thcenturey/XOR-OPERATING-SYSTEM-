"""UI-independent audit-log reading/filtering for the Activity viewer.

Kept free of GTK so it is unit-testable headlessly and reusable by a
future TUI/web viewer.  Records are shown as stored: metadata and
digests only - the broker never writes file contents to the log.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "broker", "src"))

from xor_broker.audit import read_records, verify_chain  # noqa: E402
from xor_broker.config import default_audit_path  # noqa: E402

FILTERS = ("all", "executed", "denied", "failed")


def load(audit_path: str | None = None):
    """Returns (records, chain_ok, chain_checked)."""
    path = audit_path or default_audit_path()
    records = list(read_records(path))
    ok, n, _bad = verify_chain(path)
    return records, ok, n


def apply_filter(records, name: str):
    if name == "all":
        return list(records)
    if name == "executed":
        return [r for r in records if r["result"] == "executed"]
    if name == "denied":
        return [r for r in records if r["result"].startswith("denied")]
    if name == "failed":
        return [r for r in records if r["result"] == "failed"]
    raise ValueError(f"unknown filter {name!r}")


def search(records, text: str):
    if not text:
        return list(records)
    needle = text.lower()
    return [r for r in records
            if needle in f"{r['tool']} {r['operation']} {r['resource']} "
                         f"{r['reason']} {r['capability_id'] or ''}".lower()]


def format_text_table(records) -> str:
    lines = [f"{'#':<5} {'time (UTC)':<22} {'tool':<16} {'operation':<13} "
             f"{'decision':<8} {'confirm':<9} {'result':<18} reason / resource"]
    for r in records:
        ts = r["timestamp"].replace("T", " ")[:19]
        lines.append(
            f"{r['seq']:<5} {ts:<22} {r['tool']:<16} {r['operation']:<13} "
            f"{r['decision']:<8} {str(r['confirmation']):<9} {r['result']:<18} "
            f"{r['reason']}  {r['resource']}")
    return "\n".join(lines)
