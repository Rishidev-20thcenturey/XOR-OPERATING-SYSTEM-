"""Default filesystem locations (XDG based), shared by CLI, daemon and UI.

Defaults:
  manifest : $XDG_CONFIG_HOME/xor/broker/manifest.json  (~/.config/...)
  audit log: $XDG_STATE_HOME/xor/broker/audit.jsonl     (~/.local/state/...)
  socket   : $XDG_RUNTIME_DIR/xor-broker/broker.sock
  tokens   : $XDG_STATE_HOME/xor/broker/tokens
All overridable by environment or CLI flags; tests always use temp dirs.
"""

from __future__ import annotations

import os


def _xdg(env: str, fallback: str) -> str:
    return os.environ.get(env) or os.path.expanduser(fallback)


def default_manifest_path() -> str:
    return os.environ.get("XOR_BROKER_MANIFEST") or os.path.join(
        _xdg("XDG_CONFIG_HOME", "~/.config"), "xor", "broker", "manifest.json")


def default_audit_path() -> str:
    return os.environ.get("XOR_BROKER_AUDIT") or os.path.join(
        _xdg("XDG_STATE_HOME", "~/.local/state"), "xor", "broker", "audit.jsonl")


def default_socket_path() -> str:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        return os.environ.get("XOR_BROKER_SOCKET") or os.path.join(
            runtime, "xor-broker", "broker.sock")
    return os.environ.get("XOR_BROKER_SOCKET") or os.path.join(
        _xdg("XDG_STATE_HOME", "~/.local/state"), "xor", "broker", "run",
        "broker.sock")


def default_token_dir() -> str:
    return os.environ.get("XOR_TOKEN_DIR") or os.path.join(
        _xdg("XDG_STATE_HOME", "~/.local/state"), "xor", "broker", "tokens")
