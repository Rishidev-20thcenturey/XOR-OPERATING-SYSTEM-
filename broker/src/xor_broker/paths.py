"""Safe, file-descriptor-anchored filesystem access.

Threats addressed here (see docs/THREAT_MODEL.md):

* **Path traversal** (``../../etc/passwd``) - policy canonicalises paths,
  but enforcement never trusts a string check: directory grants are
  enforced by opening the granted directory once and walking each relative
  component with ``dir_fd`` + ``O_NOFOLLOW``.  ``..`` components are
  rejected outright before any open.
* **Symlink escapes** - every component, including the final one, is
  opened with ``O_NOFOLLOW``.  The broker never follows a symlink on an
  enforced path; a symlink raises :class:`PathEscapeError` (fail closed).
* **Validation/execution races (TOCTOU)** - because traversal is anchored
  to already-open directory FDs, renaming components between the policy
  check and the open cannot redirect access outside the granted tree.

Known residual (documented, not hidden): a *hard link* inside a granted
directory can alias an inode that also exists outside the directory; FD
traversal cannot distinguish that.  File grants obtained via file
selection can pin ``(st_dev, st_ino)`` (see ``Capability.pin_*``) which
the executor verifies after opening.  Write opens never use ``O_TRUNC``;
the executor validates the target (and any pin) before it replaces or
truncates content.  Unpinned writes stage content in a private 0700
subdirectory of the target directory (anchored to the opened parent FD)
and rename it into place; pinned writes update the pinned inode in place so
the grant keeps referring to the same inode (see ``executor.py``).
"""

from __future__ import annotations

import errno
import os

from .models import ResourceScope, ScopeKind


class PathEscapeError(Exception):
    """Raised when a path would leave its granted scope (fail closed)."""


def canonicalize(path: str) -> str:
    """Absolute, symlink-resolved path. Used by the *policy* layer only."""
    return os.path.realpath(os.path.abspath(os.path.expanduser(path)))


def is_within(base_canonical: str, target_canonical: str) -> bool:
    """True if target is base or below it.  Policy-layer check only -
    enforcement is FD-based below; this never stands alone."""
    try:
        return os.path.commonpath([base_canonical]) == os.path.commonpath(
            [base_canonical, target_canonical]
        )
    except ValueError:
        return False


def scope_covers(scope: ResourceScope, canonical_target: str) -> bool:
    if scope.kind is ScopeKind.FILE:
        return scope.path == canonical_target
    return is_within(scope.path, canonical_target)


def _relative_components(base: str, target: str) -> list:
    rel = os.path.relpath(target, base)
    if rel == ".":
        return []
    parts = rel.split(os.sep)
    if any(p in ("", ".", "..") for p in parts):
        raise PathEscapeError(f"unsafe relative path: {rel!r}")
    return parts


def _open_dir_nofollow(path: str) -> int:
    try:
        return os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise PathEscapeError(f"granted directory is a symlink: {path}") from exc
        raise


def _walk_to_parent_fd(base_fd: int, components: list) -> int:
    """Walk all but the last component; returns FD of the parent dir.
    Caller owns the returned FD; ``base_fd`` is consumed on success path
    only if it differs and components walked (we always dup to be safe)."""
    FD = os.dup(base_fd)
    try:
        for comp in components[:-1]:
            try:
                next_fd = os.open(
                    comp, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=FD
                )
            except OSError as exc:
                # ELOOP: symlink with O_NOFOLLOW.  ENOTDIR: component is a
                # symlink-to-dir opened with O_DIRECTORY|O_NOFOLLOW, or a
                # regular file used as a directory.  Both fail closed.
                if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                    raise PathEscapeError(f"unsafe path component: {comp}") from exc
                raise
            os.close(FD)
            FD = next_fd
        return FD
    except BaseException:
        os.close(FD)
        raise


def open_scoped_parent(scope: ResourceScope, canonical_target: str) -> tuple:
    """Open ``canonical_target``'s parent directory strictly inside ``scope``.

    Returns ``(parent_fd, basename)``.  The caller owns ``parent_fd``.  Every
    traversed component is opened with ``dir_fd`` + ``O_NOFOLLOW`` and ``..``
    components are rejected, exactly like :func:`open_scoped_file`.  The final
    component is deliberately *not* opened here: callers that stage and rename
    must apply ``lstat``/``rename`` semantics without following it.
    """
    if scope.kind is ScopeKind.DIR:
        if not is_within(scope.path, canonical_target):
            raise PathEscapeError("target outside granted directory")
        base_fd = _open_dir_nofollow(scope.path)
        components = _relative_components(scope.path, canonical_target)
        if not components:
            os.close(base_fd)
            raise PathEscapeError("target is the granted directory, not a file")
    else:  # FILE scope: anchor at the granted file's parent directory.
        if scope.path != canonical_target:
            raise PathEscapeError("target is not the granted file")
        base_fd = _open_dir_nofollow(os.path.dirname(scope.path))
        components = [os.path.basename(scope.path)]

    try:
        parent_fd = _walk_to_parent_fd(base_fd, components)
    finally:
        os.close(base_fd)
    return parent_fd, components[-1]


def open_scoped_file(scope: ResourceScope, canonical_target: str, write: bool,
                     create: bool = True) -> int:
    """Open ``canonical_target`` strictly inside ``scope``. Returns an FD.

    Never follows symlinks. Raises PathEscapeError on any escape attempt.

    Write opens deliberately omit ``O_TRUNC``: the executor must validate the
    target (including any inode pin) *before* changing content.  ``create`` is
    false for pinned grants so a vanished selected file cannot be silently
    recreated under the same path.
    """
    parent_fd, name = open_scoped_parent(scope, canonical_target)
    flags = os.O_NOFOLLOW
    if write:
        flags |= os.O_WRONLY
        if create:
            flags |= os.O_CREAT
    else:
        flags |= os.O_RDONLY
    try:
        # Newly created files are private by default.  Existing files keep
        # their mode; the user can loosen it deliberately after the write.
        return os.open(name, flags, 0o600, dir_fd=parent_fd)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise PathEscapeError(f"final component is a symlink: {name}") from exc
        raise
    finally:
        os.close(parent_fd)


def open_scoped_dir(scope: ResourceScope, canonical_target: str) -> int:
    """Open a directory inside a DIR scope (for LIST_DIR). Returns an FD."""
    if scope.kind is not ScopeKind.DIR:
        raise PathEscapeError("list_dir requires a directory grant")
    if not is_within(scope.path, canonical_target):
        raise PathEscapeError("target outside granted directory")
    base_fd = _open_dir_nofollow(scope.path)
    components = _relative_components(scope.path, canonical_target)
    if not components:
        return base_fd
    try:
        parent_fd = _walk_to_parent_fd(base_fd, components)
        try:
            return os.open(
                components[-1],
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=parent_fd,
            )
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise PathEscapeError("symlink directory component") from exc
            raise
        finally:
            os.close(parent_fd)
    finally:
        os.close(base_fd)
