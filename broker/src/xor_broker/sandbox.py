"""Sandboxed application launching.

Bubblewrap is used as a *cooperating-tool* confinement layer: a tool gets a
private mount/network/PID namespace, a tmpfs home, and exactly two host
objects: the broker socket and its own token file.  It is not a general
syscall firewall for arbitrary same-UID processes.

The token is never placed in ``bwrap``'s command line.  The child reads it
from a read-only bind mounted file, so ``/proc/<pid>/cmdline`` does not leak
it.  The broker socket is mounted as a file, not by exposing its parent
directory (which may be ``/tmp``).
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from dataclasses import dataclass, field
from typing import List, Sequence


DEFAULT_SYSTEM_RO_BINDS: tuple[str, ...] = (
    "/usr",
    "/bin",
    "/lib",
    "/lib64",
    "/etc/alternatives",
    "/etc/ld.so.cache",
    "/etc/ssl",
)

# These are the only environment variables a sandboxed tool needs to reach
# the broker.  Everything else is cleared so host secrets cannot leak in.
BASE_ENV: tuple[tuple[str, str], ...] = (
    ("PATH", "/usr/local/bin:/usr/bin:/bin"),
    ("HOME", "/home/user"),
    ("LANG", "C.UTF-8"),
    ("TERM", "xterm-256color"),
    ("XOR_BROKER_SOCKET", "/run/xor-broker.sock"),
    ("XOR_TOOL_TOKEN_FILE", "/run/xor-token"),
)


@dataclass
class SandboxConfig:
    tool: str
    token_file: str
    socket_path: str
    command: List[str]
    network_isolated: bool = True
    mask_home: bool = True
    read_only_binds: Sequence[str] = field(
        default_factory=lambda: list(DEFAULT_SYSTEM_RO_BINDS))


def is_sandbox_supported() -> bool:
    """True if bubblewrap (bwrap) executable is available in PATH."""
    return shutil.which("bwrap") is not None


def _check_file(path: str, *, socket_expected: bool = False) -> None:
    try:
        st = os.lstat(path)
    except OSError as exc:
        raise ValueError(f"required sandbox path is unavailable: {path}: {exc}") from exc
    if socket_expected:
        if not stat.S_ISSOCK(st.st_mode):
            raise ValueError(f"broker socket is not a Unix socket: {path}")
    elif not stat.S_ISREG(st.st_mode):
        raise ValueError(f"token file is not a regular file: {path}")
    if st.st_uid != os.geteuid():
        raise ValueError(f"sandbox path is not owned by this user: {path}")


def _validate_command(command: Sequence[str]) -> None:
    if not command:
        raise ValueError("command cannot be empty")
    for arg in command:
        if not isinstance(arg, str) or not arg or "\x00" in arg:
            raise ValueError("command arguments must be non-empty strings without NUL")


def build_bubblewrap_args(config: SandboxConfig) -> List[str]:
    """Construct a bubblewrap command line for the sandboxed tool."""
    _validate_command(config.command)
    _check_file(config.token_file)
    _check_file(config.socket_path, socket_expected=True)

    bwrap = shutil.which("bwrap") or "bwrap"
    args = [
        bwrap,
        "--unshare-all",
        "--die-with-parent",
        "--new-session",
        "--clearenv",
        "--cap-drop", "ALL",
        "--proc", "/proc",
        "--dev", "/dev",
        "--tmpfs", "/tmp",
        "--tmpfs", "/run",
    ]
    if config.network_isolated:
        # --unshare-all already unshared the network; be explicit so the
        # intent is readable in the process table.
        args.append("--unshare-net")
    else:
        args.append("--share-net")
    if config.mask_home:
        args.extend(["--tmpfs", "/home", "--dir", "/home/user"])

    seen = set()
    for path in config.read_only_binds:
        if not isinstance(path, str) or not path.startswith("/"):
            raise ValueError(f"read-only bind must be an absolute path: {path!r}")
        if path not in DEFAULT_SYSTEM_RO_BINDS:
            raise ValueError(f"unsupported read-only bind: {path!r}")
        if path in seen or not os.path.exists(path):
            continue
        seen.add(path)
        # /bin, /lib and /lib64 are usually symlinks into /usr on Debian.
        # Bind the real source at the conventional destination so the
        # sandbox has working absolute paths without exposing more of /.
        args.extend(["--ro-bind", os.path.realpath(path), path])

    # Only the socket inode and the tool's own token are exposed.  Binding a
    # file (rather than its parent) means a /tmp socket does not expose /tmp.
    args.extend(["--bind", config.socket_path, "/run/xor-broker.sock"])
    args.extend(["--ro-bind", config.token_file, "/run/xor-token"])
    for key, value in BASE_ENV:
        args.extend(["--setenv", key, value])
    args.append("--")
    args.extend(config.command)
    return args


def launch_sandboxed(config: SandboxConfig) -> subprocess.Popen:
    """Start the sandboxed command and return the bwrap process."""
    if not is_sandbox_supported():
        raise RuntimeError("bubblewrap (bwrap) is not installed")
    return subprocess.Popen(build_bubblewrap_args(config))
