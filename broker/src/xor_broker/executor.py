"""Execution of authorised actions - the only place that touches files.

The executor re-verifies everything safety-critical instead of trusting
upstream checks (defence in depth):

* For writes it requires a valid, unconsumed :class:`ApprovalToken`
  whose digest matches the *actual* request being executed.
* Files are opened through ``paths.open_scoped_*`` (FD-anchored,
  ``O_NOFOLLOW``), so approval-time validation cannot be raced.
* Inode pins from file/directory-selection grants are checked with
  ``fstat`` before any content is read, listed, written or truncated.
* Unpinned writes are staged in a private 0700 directory anchored to the
  target's parent FD, fsynced, re-checked (source and destination) and
  renamed into place, so a failed write leaves the original file untouched
  and other users cannot read or substitute the staged bytes.  The original
  rwx permission bits are copied to the staged file; ownership, ACLs,
  extended attributes and setuid/setgid bits are not (documented in
  ``broker/README.md``).
* Pinned writes update the pinned inode in place: replacing it would break
  the grant's identity guarantee.  The old content is copied to an
  anonymous backup first, new bytes are written from offset zero, and the
  file is truncated to the new length only after all new bytes are
  accepted.  A failed target write restores the backup; a failed restore
  is reported explicitly.  This is rollback, not crash-atomicity.
* Zero-progress or oversized ``os.write`` results fail explicitly instead
  of looping or corrupting the stream.
"""

from __future__ import annotations

import errno
import os
import secrets
import stat
import tempfile
from datetime import datetime
from typing import Optional

from .confirm import ApprovalToken
from .models import ActionRequest, Capability, Operation, Outcome, OutcomeStatus, ReasonCode
from .paths import (
    PathEscapeError, open_scoped_dir, open_scoped_file, open_scoped_parent,
)

MAX_READ_BYTES = 16 * 1024 * 1024  # broker reads are bounded; no huge blobs
_COPY_CHUNK = 64 * 1024
_TEMP_PREFIX = ".xor-write-"


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
            if capability.pin_ino is None:
                _atomic_replace(capability, canonical_path, request.content)
            else:
                _write_pinned(capability, canonical_path, request.content)
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


def _atomic_replace(capability: Capability, canonical_path: str, content: bytes) -> None:
    """Stage an unpinned write and rename it over the target atomically.

    There is no inode pin to preserve, so replacing the directory entry is
    safe and gives failure atomicity.  Staging happens in a private 0700
    directory anchored to the target's parent FD, so another user cannot
    read the staged bytes or substitute the source entry.  The target is
    opened without ``O_TRUNC`` (and without ``O_CREAT``) first to prove it
    is a writable regular file and to capture its mode.  If it is missing,
    only the directory's write permission is needed to create the
    replacement.
    """
    parent_fd, name = open_scoped_parent(capability.resource, canonical_path)
    try:
        existing_fd = _open_existing_regular(parent_fd, name)
        try:
            if existing_fd is None:
                expected = None
                mode = 0o600
            else:
                st = os.fstat(existing_fd)
                expected = (st.st_dev, st.st_ino)
                # Carry ordinary rwx bits.  Do not re-create setuid/setgid
                # on a freshly staged file whose content came from a tool.
                mode = stat.S_IMODE(st.st_mode) & 0o777

            # Stage inside a private 0700 directory anchored to the opened
            # parent FD.  The directory is created inside the target's parent
            # so the final rename stays on the same filesystem.  Other users
            # cannot enter it or replace the source entry by pathname.
            stage_fd, stage_name = _open_stage_dir(parent_fd)
            stage_id = _identity(stage_fd)
            try:
                temp_fd, temp_name = _open_temp(stage_fd)
                try:
                    try:
                        _write_all(temp_fd, content)
                        os.fchmod(temp_fd, mode)
                        os.fsync(temp_fd)
                        source_id = _identity(temp_fd)
                    finally:
                        os.close(temp_fd)

                    # The private directory is the primary control.  The
                    # source identity check is defence in depth against
                    # same-user substitution, which is outside the broker's
                    # cross-user boundary.
                    _verify_source_unchanged(stage_fd, temp_name, source_id)
                    # Re-check immediately before the rename: a symlink or a
                    # different file must not be silently replaced.
                    _verify_target_unchanged(parent_fd, name, expected)
                    os.rename(temp_name, name, src_dir_fd=stage_fd,
                              dst_dir_fd=parent_fd)
                    temp_name = None
                    try:
                        os.fsync(parent_fd)
                    except OSError:
                        # The file data is already fsynced; directory fsync
                        # is best-effort durability, not a correctness
                        # guarantee.
                        pass
                finally:
                    if temp_name is not None:
                        _unlink_quiet(stage_fd, temp_name)
            finally:
                os.close(stage_fd)
                _rmdir_stage_quiet(parent_fd, stage_name, stage_id)
        finally:
            if existing_fd is not None:
                os.close(existing_fd)
    finally:
        os.close(parent_fd)


def _write_pinned(capability: Capability, canonical_path: str, content: bytes) -> None:
    """Write to the exact pinned inode, with rollback on failure.

    Renaming a staged file over the target would change its inode and break
    the grant's pin (and hard links to other names).  Instead the new bytes
    are staged anonymously and the old bytes backed up, then the pinned FD is
    overwritten from offset zero and truncated to the new length only after
    every new byte is accepted.  Any reported write failure restores the old
    bytes.  A crash in the middle of the in-place update is still not
    crash-atomic; that residual is documented.
    """
    write_fd = open_scoped_file(capability.resource, canonical_path,
                                write=True, create=False)
    try:
        _check_pin(write_fd, capability)
        if not stat.S_ISREG(os.fstat(write_fd).st_mode):
            raise OSError(errno.EINVAL, "write target is not a regular file")

        # Separate read FD for the rollback copy.  If the read permission is
        # missing we fail *before* modifying the target.
        read_fd = open_scoped_file(capability.resource, canonical_path,
                                   write=False, create=False)
        try:
            _check_pin(read_fd, capability)
            _write_pinned_in_place(write_fd, read_fd, content)
        finally:
            os.close(read_fd)
    finally:
        os.close(write_fd)


def _write_pinned_in_place(write_fd: int, read_fd: int, content: bytes) -> None:
    with tempfile.TemporaryFile(prefix="xor-write-stage-") as staged, \
            tempfile.TemporaryFile(prefix="xor-write-backup-") as backup:
        _write_all(staged.fileno(), content)
        os.fsync(staged.fileno())
        staged.seek(0)

        _copy_fd(read_fd, backup.fileno())
        os.fsync(backup.fileno())
        backup.seek(0)
        backup_size = os.fstat(backup.fileno()).st_size

        try:
            # Overwrite from offset zero and truncate only after every new
            # byte has been accepted.  A zero-progress failure on the first
            # write therefore leaves the old content untouched even before
            # the backup is restored.
            os.lseek(write_fd, 0, os.SEEK_SET)
            _copy_fd(staged.fileno(), write_fd)
            os.ftruncate(write_fd, len(content))
            os.fsync(write_fd)
        except BaseException as exc:
            try:
                os.lseek(write_fd, 0, os.SEEK_SET)
                _copy_fd(backup.fileno(), write_fd)
                os.ftruncate(write_fd, backup_size)
                os.fsync(write_fd)
            except BaseException as restore_exc:
                raise OSError(
                    errno.EIO,
                    f"write failed ({exc}); rollback failed ({restore_exc})",
                ) from exc
            raise


def _write_all(FD: int, data) -> None:
    """Write all of ``data`` or raise; never spin on a zero-byte write."""
    view = memoryview(data)
    while view:
        written = os.write(FD, view)
        if written is None:
            raise OSError(errno.EIO, "write returned no byte count")
        if not isinstance(written, int) or written <= 0:
            raise OSError(errno.EIO,
                          f"write made no progress ({written!r} bytes)")
        if written > len(view):
            raise OSError(errno.EIO,
                          f"write reported {written} bytes for a {len(view)}-byte buffer")
        view = view[written:]


def _copy_fd(src_fd: int, dst_fd: int) -> None:
    """Copy every byte from ``src_fd`` to ``dst_fd`` from offset zero."""
    os.lseek(src_fd, 0, os.SEEK_SET)
    while True:
        chunk = os.read(src_fd, _COPY_CHUNK)
        if not chunk:
            return
        _write_all(dst_fd, chunk)


def _open_temp(parent_fd: int):
    """Create a private temporary file in ``parent_fd`` for staging."""
    for _ in range(16):
        name = f"{_TEMP_PREFIX}{os.getpid()}-{secrets.token_hex(8)}"
        try:
            fd = os.open(
                name,
                os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=parent_fd,
            )
        except FileExistsError:
            continue
        return fd, name
    raise OSError(errno.EEXIST, "could not allocate temporary write file")


def _open_stage_dir(parent_fd: int):
    """Create and open a private 0700 staging directory in ``parent_fd``.

    Staging inside the target's parent keeps the final rename on the same
    filesystem.  The directory's identity is captured immediately after
    ``mkdir`` and re-checked through the opened FD.  Setup-failure cleanup
    removes the name only while it still resolves to that exact directory;
    otherwise it is left as an orphan rather than deleting another entry.
    """
    for _ in range(16):
        name = f"{_TEMP_PREFIX}dir-{os.getpid()}-{secrets.token_hex(8)}"
        try:
            os.mkdir(name, 0o700, dir_fd=parent_fd)
        except FileExistsError:
            continue

        # Capture identity as early as possible.  A different user could
        # already have swapped the entry, so only trust a directory owned by
        # this effective UID.
        created = _lstat_target(parent_fd, name)
        if created is None:
            # The entry vanished before its identity could be captured.
            # Nothing verifiable to clean up; try a fresh name.
            continue
        if not stat.S_ISDIR(created.st_mode) or created.st_uid != os.geteuid():
            raise OSError(errno.EPERM, "unsafe staging directory")
        created_id = (created.st_dev, created.st_ino)

        fd = None
        try:
            fd = os.open(
                name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=parent_fd,
            )
            st = os.fstat(fd)
            if (st.st_dev, st.st_ino) != created_id:
                raise OSError(errno.EPERM,
                              "staging directory changed during setup")
            if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid():
                raise OSError(errno.EPERM, "unsafe staging directory")
            if st.st_mode & 0o077:
                raise OSError(errno.EPERM, "staging directory is not private")
            return fd, name
        except BaseException:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            _rmdir_stage_quiet(parent_fd, name, created_id)
            raise
    raise OSError(errno.EEXIST, "could not allocate staging directory")


def _identity(fd: int) -> tuple:
    st = os.fstat(fd)
    return st.st_dev, st.st_ino


def _verify_source_unchanged(stage_fd: int, name: str, expected: tuple) -> None:
    """Defence in depth: the staged source must still be our regular file."""
    try:
        st = os.stat(name, dir_fd=stage_fd, follow_symlinks=False)
    except FileNotFoundError:
        raise OSError(errno.ESTALE, "staged write file disappeared") from None
    if not stat.S_ISREG(st.st_mode):
        raise OSError(errno.ESTALE, "staged write file is not regular")
    if (st.st_dev, st.st_ino) != expected:
        raise OSError(errno.ESTALE, "staged write file changed")


def _open_existing_regular(parent_fd: int, name: str):
    """Open an existing target for a writability/type check, no truncation.

    Returns ``None`` when the entry does not exist.  A symlink or non-regular
    entry fails closed (``PathEscapeError`` / ``EINVAL``).  ``O_NONBLOCK``
    prevents a raced FIFO from blocking the broker.
    """
    st = _lstat_target(parent_fd, name)
    if st is None:
        return None
    _require_regular(st, name)
    try:
        fd = os.open(name, os.O_WRONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                     dir_fd=parent_fd)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise PathEscapeError(
                f"final component is a symlink: {name}") from exc
        raise
    try:
        _require_regular(os.fstat(fd), name)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _verify_target_unchanged(parent_fd: int, name: str, expected) -> None:
    st = _lstat_target(parent_fd, name)
    if expected is None:
        if st is not None:
            _require_regular(st, name)
            raise OSError(errno.EEXIST, "write target appeared during staging")
        return
    if st is None:
        raise OSError(errno.ESTALE, "write target disappeared during staging")
    _require_regular(st, name)
    if (st.st_dev, st.st_ino) != expected:
        raise OSError(errno.ESTALE, "write target changed during staging")


def _lstat_target(parent_fd: int, name: str):
    try:
        return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _require_regular(st, name: str) -> None:
    if stat.S_ISLNK(st.st_mode):
        raise PathEscapeError(f"final component is a symlink: {name}")
    if not stat.S_ISREG(st.st_mode):
        raise OSError(errno.EINVAL, "write target is not a regular file")


def _unlink_quiet(parent_fd: int, name: str) -> None:
    # Best-effort cleanup must not mask the original write error.
    try:
        os.unlink(name, dir_fd=parent_fd)
    except OSError:
        pass


def _rmdir_quiet(parent_fd: int, name: str) -> None:
    try:
        os.rmdir(name, dir_fd=parent_fd)
    except OSError:
        pass


def _rmdir_stage_quiet(parent_fd: int, name: str, expected: tuple) -> None:
    """Remove the staging directory only while it is still verifiably ours.

    The identity check and ``rmdir`` cannot be made atomic portably.  If
    other users can rename entries in the parent, or a sticky parent is
    owned by an untrusted user, leave the empty directory instead of risking
    deletion of a substituted entry.
    """
    try:
        parent_st = os.fstat(parent_fd)
        st = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError:
        return
    if not stat.S_ISDIR(st.st_mode):
        return
    if (st.st_dev, st.st_ino) != expected:
        return
    if st.st_uid != os.geteuid():
        return
    if not _parent_allows_rmdir(parent_st):
        return
    _rmdir_quiet(parent_fd, name)


def _parent_allows_rmdir(parent_st) -> bool:
    """Whether a name in this parent can be removed without a cross-user race.

    Without write permission for group/other, other users cannot rename our
    entry.  In a sticky directory they cannot remove or rename entries they
    do not own, unless they own the directory itself; an untrusted directory
    owner is therefore not treated as safe.
    """
    if not (parent_st.st_mode & 0o022):
        return True
    if not (parent_st.st_mode & stat.S_ISVTX):
        return False
    return parent_st.st_uid in (os.geteuid(), 0)


def _fail(request, capability, message: str) -> Outcome:
    return Outcome(OutcomeStatus.FAILED, ReasonCode.EXECUTION_ERROR,
                   request.request_id, capability.capability_id, error=message)
