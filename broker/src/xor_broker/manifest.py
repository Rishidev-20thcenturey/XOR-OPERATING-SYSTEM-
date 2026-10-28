"""Capability manifest loading and strict validation.

A manifest is a JSON document listing explicit grants.  Validation fails
closed: any malformed entry aborts the whole load (a partially loaded
policy is a policy nobody reviewed).  Unsupported operations
(delete/network/exec) cannot be granted at all in the baseline.
"""

from __future__ import annotations

import json
import os
import stat
from datetime import datetime
from typing import List

from .models import (
    Capability,
    Operation,
    ResourceScope,
    ScopeKind,
    SUPPORTED_OPERATIONS,
    VALID_RIGHTS,
)
from .paths import canonicalize


class ManifestError(ValueError):
    pass


def _req(entry: dict, key: str, where: str):
    if key not in entry:
        raise ManifestError(f"{where}: missing required field {key!r}")
    return entry[key]


def parse_manifest(data: object) -> List[Capability]:
    if not isinstance(data, dict) or "capabilities" not in data:
        raise ManifestError("manifest must be an object with a 'capabilities' list")
    entries = data["capabilities"]
    if not isinstance(entries, list):
        raise ManifestError("'capabilities' must be a list")

    capabilities: List[Capability] = []
    seen_ids = set()
    for i, entry in enumerate(entries):
        where = f"capabilities[{i}]"
        if not isinstance(entry, dict):
            raise ManifestError(f"{where}: entry must be an object")

        cap_id = _req(entry, "capability_id", where)
        tool = _req(entry, "tool", where)
        op_raw = _req(entry, "operation", where)
        resource = _req(entry, "resource", where)
        rights_raw = _req(entry, "rights", where)

        if not isinstance(cap_id, str) or not cap_id:
            raise ManifestError(f"{where}: capability_id must be a non-empty string")
        if cap_id in seen_ids:
            raise ManifestError(f"{where}: duplicate capability_id {cap_id!r}")
        seen_ids.add(cap_id)
        if not isinstance(tool, str) or not tool:
            raise ManifestError(f"{where}: tool must be a non-empty string")

        try:
            operation = Operation(op_raw)
        except ValueError:
            raise ManifestError(f"{where}: unknown operation {op_raw!r}") from None
        if operation not in SUPPORTED_OPERATIONS:
            raise ManifestError(
                f"{where}: operation {op_raw!r} is not grantable in the baseline"
            )

        if not isinstance(resource, dict):
            raise ManifestError(f"{where}: resource must be an object")
        kind_raw = _req(resource, "type", where + ".resource")
        path_raw = _req(resource, "path", where + ".resource")
        try:
            kind = ScopeKind(kind_raw)
        except ValueError:
            raise ManifestError(f"{where}: resource.type must be 'file' or 'dir'") from None
        if not isinstance(path_raw, str) or not path_raw or "\x00" in path_raw:
            raise ManifestError(f"{where}: resource.path must be a non-empty string")
        if operation is Operation.LIST_DIR and kind is not ScopeKind.DIR:
            # The executor only lists directory-scoped grants.  Rejecting a
            # mismatched grant here prevents a policy allow followed by an
            # execution-time failure.
            raise ManifestError(
                f"{where}: list_dir requires resource.type 'dir'")
        # Grants are stored canonical so policy comparisons are exact.
        scope = ResourceScope(kind=kind, path=canonicalize(path_raw))

        if not isinstance(rights_raw, list) or not rights_raw:
            raise ManifestError(f"{where}: rights must be a non-empty list")
        rights = frozenset(rights_raw)
        if not rights <= VALID_RIGHTS:
            raise ManifestError(f"{where}: invalid rights {sorted(rights - VALID_RIGHTS)}")

        expires_at = None
        if entry.get("expires_at") is not None:
            try:
                expires_at = datetime.fromisoformat(entry["expires_at"].replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                raise ManifestError(f"{where}: expires_at must be ISO-8601") from None
            if expires_at.tzinfo is None:
                raise ManifestError(f"{where}: expires_at must include a timezone")

        max_uses = entry.get("max_uses")
        if max_uses is not None and (not isinstance(max_uses, int) or max_uses < 1):
            raise ManifestError(f"{where}: max_uses must be a positive integer or null")

        capabilities.append(
            Capability(
                capability_id=cap_id,
                tool=tool,
                operation=operation,
                resource=scope,
                rights=rights,
                expires_at=expires_at,
                max_uses=max_uses,
                pin_dev=entry.get("pin_dev"),
                pin_ino=entry.get("pin_ino"),
            )
        )
    return capabilities


def open_manifest_fd(path: str) -> int:
    """Open an existing manifest without following links or blocking on FIFOs.

    Manifests are trusted configuration.  Refusing symlinks and
    non-regular files at the shared loader keeps this policy consistent
    with ``xor-broker init`` and prevents a path swap from redirecting a
    grant load to a special file.
    """
    try:
        fd = os.open(
            path,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
        )
    except OSError as exc:
        raise ManifestError(f"{path}: manifest cannot be opened safely: {exc}") from exc
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise ManifestError(f"{path}: manifest is not a regular file")
    except BaseException:
        os.close(fd)
        raise
    return fd


def load_manifest(path: str) -> List[Capability]:
    fd = open_manifest_fd(path)
    try:
        with os.fdopen(fd, "r", encoding="utf-8") as fh:
            try:
                data = json.load(fh)
            except json.JSONDecodeError as exc:
                raise ManifestError(f"{path}: invalid JSON: {exc}") from exc
            except (OSError, UnicodeError) as exc:
                raise ManifestError(
                    f"{path}: manifest cannot be read: {exc}") from exc
    except ManifestError:
        raise
    except OSError as exc:
        raise ManifestError(f"{path}: manifest cannot be read: {exc}") from exc
    return parse_manifest(data)


def grant_for_selected_file(tool: str, operation: Operation, selected_path: str,
                            capability_id: str) -> Capability:
    """Build a narrowly scoped capability from a user selection.

    A ``LIST_DIR`` selection must name a directory and produces a
    directory-scoped grant; other operations produce an exact file grant.
    Only the selected path is granted - never its parent directory.  The
    selected inode is pinned so replacing a file later cannot redirect
    the grant.
    """
    if operation not in SUPPORTED_OPERATIONS:
        raise ValueError(f"{operation.value} is not grantable in the baseline")
    canonical = canonicalize(selected_path)
    st = os.stat(canonical)  # follows symlinks at *selection* time; the
    # canonical target is what was selected and is what gets pinned.
    if operation is Operation.LIST_DIR:
        if not stat.S_ISDIR(st.st_mode):
            raise ValueError("list_dir selection must be a directory")
        kind = ScopeKind.DIR
        rights = frozenset({"read"})
    else:
        if not stat.S_ISREG(st.st_mode):
            raise ValueError(f"{operation.value} selection must be a regular file")
        kind = ScopeKind.FILE
        rights = (frozenset({"read"})
                  if operation is Operation.READ_FILE
                  else frozenset({"write"}))
    return Capability(
        capability_id=capability_id,
        tool=tool,
        operation=operation,
        resource=ResourceScope(kind, canonical),
        rights=rights,
        pin_dev=st.st_dev,
        pin_ino=st.st_ino,
    )
