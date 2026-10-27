"""Unix-socket broker daemon + client: the IPC boundary used by tools.

Protocol: newline-delimited JSON.  Request::

    {"tool": "demo-reader", "operation": "read_file", "path": "/abs/path",
     "content_b64": "...",            # writes only
     "description": "human summary"}

Response::

    {"status": "executed|denied_policy|denied_user|denied_unsupported|failed",
     "reason": "...", "capability_id": "..."|null,
     "content_b64": "...", "listing": [...]}

Identity honesty: the daemon reports the peer UID/PID (SO_PEERCRED) in
its response, but the *tool name is self-declared* in the baseline - any
same-UID process could claim another tool's name.  Capability checks
therefore separate cooperating tools from each other but are not yet a
hard identity boundary.  Authenticating tool identity (e.g. via
Flatpak/bubblewrap instance IDs) is Milestone 4 work and is documented
as such.
"""

from __future__ import annotations

import base64
import fcntl
import json
import os
import socket
import struct

from .broker import Broker
from .models import ActionRequest, Operation

DEFAULT_CONNECTION_TIMEOUT = 30.0
# A write can legitimately wait for the user's confirmation dialog, which
# has a 120 s TTL.  The client must outlast that, not time out at 30 s.
DEFAULT_REQUEST_TIMEOUT = 180.0


class BrokerError(Exception):
    pass


def _peercred(conn: socket.socket):
    try:
        data = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        pid, uid, gid = struct.unpack("3i", data)
        return {"pid": pid, "uid": uid, "gid": gid}
    except OSError:
        return None


def serve(broker: Broker, socket_path: str,
          connection_timeout: float = DEFAULT_CONNECTION_TIMEOUT) -> None:  # pragma: no cover - loop
    """Serve until interrupted.

    A sibling lock file makes the daemon a singleton: a second instance
    fails instead of unlinking the live socket.  Per-connection timeouts
    stop a silent client from holding the single-threaded loop forever.
    """
    lock_path = socket_path + ".lock"
    lock_fd = os.open(lock_path,
                      os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    try:
        try:
            os.fchmod(lock_fd, 0o600)
        except OSError:
            pass
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BrokerError(
                f"another xor-broker is already serving on {socket_path}") from exc

        if os.path.exists(socket_path):
            os.unlink(socket_path)
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        old_umask = os.umask(0o077)
        try:
            srv.bind(socket_path)
        finally:
            os.umask(old_umask)
        os.chmod(socket_path, 0o600)
        srv.listen(8)
        try:
            while True:
                conn, _ = srv.accept()
                with conn:
                    try:
                        conn.settimeout(connection_timeout)
                        _handle_conn(broker, conn)
                    except (ConnectionError, OSError):
                        # A timed-out/broken client must not kill the daemon.
                        continue
        finally:
            srv.close()
            if os.path.exists(socket_path):
                os.unlink(socket_path)
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(lock_fd)


def _handle_conn(broker: Broker, conn: socket.socket) -> None:
    buf = b""
    while b"\n" not in buf and len(buf) < 32 * 1024 * 1024:
        chunk = conn.recv(65536)
        if not chunk:
            return
        buf += chunk
    try:
        req = json.loads(buf.split(b"\n")[0].decode("utf-8"))
        outcome = broker.handle(_request_from_wire(req))
        resp = {
            "status": outcome.status.value,
            "reason": outcome.reason.value,
            "capability_id": outcome.capability_id,
            "content_b64": base64.b64encode(outcome.data).decode() if outcome.data else None,
            "listing": outcome.listing,
            "error": outcome.error,
            "peer": _peercred(conn),
        }
    except Exception as exc:  # malformed wire requests denied, daemon survives
        resp = {"status": "failed", "reason": "execution_error",
                "error": f"malformed request: {exc}"}
    conn.sendall((json.dumps(resp) + "\n").encode("utf-8"))


def _request_from_wire(req: dict) -> ActionRequest:
    content = req.get("content_b64")
    return ActionRequest(
        tool=str(req["tool"]),
        operation=Operation(req["operation"]),
        path=str(req.get("path", "")),
        content=base64.b64decode(content) if content else None,
        description=str(req.get("description", "")),
    )


class BrokerClient:
    """Client used by cooperating tools (and the future assistant runner).
    This is the *only* supported route to broker-mediated resources."""

    def __init__(self, tool: str, socket_path: str,
                 timeout: float = DEFAULT_REQUEST_TIMEOUT):
        self.tool = tool
        self.socket_path = socket_path
        self.timeout = timeout

    def _call(self, operation: Operation, path: str, content=None,
              description: str = "") -> dict:
        payload = {
            "tool": self.tool,
            "operation": operation.value,
            "path": path,
            "content_b64": base64.b64encode(content).decode() if content is not None else None,
            "description": description,
        }
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(self.timeout)
            sock.connect(self.socket_path)
            sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))
            buf = b""
            while b"\n" not in buf:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                buf += chunk
        if not buf:
            raise BrokerError("empty response from broker")
        return json.loads(buf.split(b"\n")[0].decode("utf-8"))

    def read_file(self, path: str) -> bytes:
        resp = self._call(Operation.READ_FILE, path)
        if resp["status"] != "executed":
            raise BrokerError(f"read denied: {resp['reason']}")
        return base64.b64decode(resp["content_b64"] or "")

    def write_file(self, path: str, content: bytes, description: str = "") -> dict:
        return self._call(Operation.WRITE_FILE, path, content, description)

    def list_dir(self, path: str) -> list:
        resp = self._call(Operation.LIST_DIR, path)
        if resp["status"] != "executed":
            raise BrokerError(f"list denied: {resp['reason']}")
        return resp["listing"] or []
