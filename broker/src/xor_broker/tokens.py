"""Per-tool bearer tokens for the Unix-socket broker.

The token is a capability held by a cooperating tool, not a replacement for
OS-level isolation.  Tokens live in a private directory as one 0600 file per
tool so a sandbox launcher and the daemon can share them without putting the
secret in a process command line.  A same-UID process can still read the
directory; the sandbox is what keeps other tools out.  See THREAT_MODEL T11.
"""

from __future__ import annotations

import os
import re
import secrets
import stat
from typing import Optional

TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class TokenStoreError(ValueError):
    pass


def validate_tool_name(tool: str) -> str:
    if not isinstance(tool, str) or not TOOL_NAME_RE.fullmatch(tool):
        raise TokenStoreError(
            "tool name must be 1-128 characters from [A-Za-z0-9_.-]")
    return tool


def token_path(directory: str, tool: str) -> str:
    return os.path.join(os.path.abspath(os.path.expanduser(directory)),
                        validate_tool_name(tool))


def read_token_file(path: str) -> Optional[str]:
    """Read a token file without following links or blocking on FIFOs."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC |
                     os.O_NONBLOCK)
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > 4096:
            return None
        data = os.read(fd, 4096)
    except OSError:
        return None
    finally:
        os.close(fd)
    try:
        token = data.decode("ascii").strip()
    except UnicodeDecodeError:
        return None
    return token or None


class TokenStore:
    """Private directory of per-tool bearer tokens."""

    def __init__(self, directory: str):
        self.directory = os.path.abspath(os.path.expanduser(directory))
        try:
            st = os.lstat(self.directory)
        except FileNotFoundError:
            os.makedirs(self.directory, mode=0o700)
            st = os.lstat(self.directory)
        if not stat.S_ISDIR(st.st_mode):
            raise TokenStoreError(f"token store is not a directory: {self.directory}")
        if st.st_uid != os.geteuid():
            raise TokenStoreError(f"token store is not owned by this user: {self.directory}")
        try:
            os.chmod(self.directory, 0o700)
        except OSError as exc:
            raise TokenStoreError(f"cannot secure token store: {exc}") from exc

    def path_for(self, tool: str) -> str:
        return token_path(self.directory, tool)

    def issue(self, tool: str, token: Optional[str] = None) -> str:
        path = self.path_for(tool)
        if token is None:
            token = secrets.token_hex(32)
        if (not isinstance(token, str) or not token or len(token) > 4096
                or any(ch.isspace() for ch in token)):
            raise TokenStoreError("token must be a non-empty token without whitespace")
        try:
            data = token.encode("ascii")
        except UnicodeEncodeError as exc:
            raise TokenStoreError("token must be ASCII") from exc
        temp = os.path.join(self.directory,
                            f".{validate_tool_name(tool)}.{secrets.token_hex(8)}.tmp")
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                     os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            while data:
                written = os.write(fd, data)
                if written <= 0:
                    raise OSError("short token write")
                data = data[written:]
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            os.replace(temp, path)
            os.chmod(path, 0o600, follow_symlinks=False)
        finally:
            try:
                os.unlink(temp)
            except FileNotFoundError:
                pass
        return token

    def get(self, tool: str) -> Optional[str]:
        try:
            path = self.path_for(tool)
        except TokenStoreError:
            return None
        return read_token_file(path)

    def revoke(self, tool: str) -> None:
        try:
            os.unlink(self.path_for(tool))
        except FileNotFoundError:
            pass

    def has_tokens(self) -> bool:
        try:
            entries = os.scandir(self.directory)
        except OSError:
            return False
        with entries:
            for entry in entries:
                if entry.name.startswith("."):
                    continue
                if entry.is_file(follow_symlinks=False):
                    return True
        return False
