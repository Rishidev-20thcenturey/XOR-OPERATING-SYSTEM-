#!/usr/bin/env python3
"""Collect diagnostic files from a QEMU guest agent.

This is a manual debugging helper for the ISO smoke-test workflow.  It is not
used by the production image, the normal test suite, or the release gate.

Usage:
    python3 tools/qga-query.py /path/to/qga.sock output-directory
"""

from __future__ import annotations

import argparse
import base64
import json
import socket
import sys
import time
from pathlib import Path
from typing import Any


COMMANDS: tuple[tuple[str, str], ...] = (
    (
        "lightdm-status",
        "systemctl --no-pager --full status lightdm.service 2>&1 || true",
    ),
    (
        "display-manager-status",
        "systemctl --no-pager --full status display-manager.service 2>&1 || true",
    ),
    (
        "lightdm-journal",
        "journalctl -b --no-pager -u lightdm.service "
        "-u display-manager.service -n 400 2>&1 || true",
    ),
    (
        "warnings",
        "journalctl -b --no-pager -p warning -n 400 2>&1 || true",
    ),
    (
        "lightdm-log",
        "cat /var/log/lightdm/lightdm.log 2>&1 || true",
    ),
    (
        "xorg-log",
        "cat /var/log/lightdm/x-0.log /var/log/Xorg.0.log 2>&1 || true",
    ),
    (
        "jobs",
        "systemctl --no-pager --full list-jobs 2>&1 || true",
    ),
    (
        "graphics",
        "ls -l /dev/dri /dev/fb0 2>&1 || true; "
        "cat /sys/class/graphics/fb0/name 2>&1 || true; "
        "dmesg | grep -i -E 'drm|fb0|virtio|vga' | tail -n 80 2>&1 || true",
    ),
    (
        "sessions",
        "loginctl --no-pager list-sessions 2>&1 || true; "
        "systemctl is-active graphical.target lightdm.service 2>&1 || true; "
        "systemctl get-default 2>&1 || true",
    ),
    (
        "processes",
        "ps -ef 2>&1 || true",
    ),
    (
        "autostart",
        "echo HOME=$HOME; id; echo DISPLAY=$DISPLAY; "
        "echo XDG_CURRENT_DESKTOP=$XDG_CURRENT_DESKTOP; "
        "echo '--- system autostart ---'; ls -la /etc/xdg/autostart 2>&1 || true; "
        "cat /etc/xdg/autostart/xor-shell-autostart.desktop 2>&1 || true; "
        "cat /etc/xdg/autostart/xor-broker-autostart.desktop 2>&1 || true; "
        "echo '--- user autostart ---'; "
        "ls -la /home/user/.config/autostart 2>&1 || true; "
        "cat /home/user/.config/autostart/*.desktop 2>&1 || true",
    ),
    (
        "xsession-errors",
        "cat /home/user/.xsession-errors 2>&1 || true; "
        "ls -la /home/user 2>&1 || true",
    ),
    (
        "user-journal",
        "runuser -u user -- env XDG_RUNTIME_DIR=/run/user/1000 "
        "journalctl --user -b --no-pager -n 400 2>&1 || true; "
        "runuser -u user -- env XDG_RUNTIME_DIR=/run/user/1000 "
        "systemctl --user --no-pager --failed 2>&1 || true",
    ),
    (
        "xfce-config",
        "find /home/user/.config/xfce4 -maxdepth 5 -type f "
        "-printf '%p %s bytes\\n' 2>&1 || true; "
        "echo '--- user desktop xml ---'; "
        "cat /home/user/.config/xfce4/xfconf/xfce-perchannel-xml/"
        "xfce4-desktop.xml 2>&1 || true; "
        "echo '--- skel desktop xml ---'; "
        "cat /etc/skel/.config/xfce4/xfconf/xfce-perchannel-xml/"
        "xfce4-desktop.xml 2>&1 || true",
    ),
    (
        "xfce-props",
        "runuser -u user -- env DISPLAY=:0 XDG_RUNTIME_DIR=/run/user/1000 "
        "xfconf-query -c xfce4-desktop -l -v 2>&1 || true; "
        "runuser -u user -- env DISPLAY=:0 XDG_RUNTIME_DIR=/run/user/1000 "
        "xfconf-query -c xfce4-desktop "
        "-p /backdrop/screen0/monitor0/workspace0/last-image 2>&1 || true; "
        "runuser -u user -- env DISPLAY=:0 XDG_RUNTIME_DIR=/run/user/1000 "
        "xrandr --query 2>&1 || true",
    ),
    (
        "wallpaper",
        "echo '--- installed wallpapers ---'; "
        "ls -l /usr/share/xor/wallpapers/ 2>&1 || true; "
        "for f in /usr/share/xor/wallpapers/*; do "
        "test -f \"$f\" && { echo \"$f\"; "
        "stat -c 'size=%s mode=%a owner=%U:%G' \"$f\"; "
        "od -An -tx1 -N3 \"$f\"; }; done 2>&1 || true; "
        "echo '--- xfdesktop processes ---'; "
        "ps -ef | grep -E 'xfdesktop|xor[_-]shell' | grep -v grep 2>&1 || true; "
        "echo '--- xfdesktop journal ---'; "
        "runuser -u user -- env XDG_RUNTIME_DIR=/run/user/1000 "
        "journalctl --user -b --no-pager -n 800 2>&1 | "
        "grep -Ei 'xfdesktop|backdrop|wallpaper|image' | tail -n 120 || true",
    ),
    (
        "wallpaper-probe",
        "echo '--- JPEG loader ---'; "
        "gdk-pixbuf-query-loaders 2>&1 | grep -i jpeg || true; "
        "runuser -u user -- env DISPLAY=:0 XDG_RUNTIME_DIR=/run/user/1000 "
        "python3 -c \"import gi; gi.require_version('GdkPixbuf','2.0'); "
        "from gi.repository import GdkPixbuf; "
        "p=GdkPixbuf.Pixbuf.new_from_file("
        "'/usr/share/xor/wallpapers/xor-royal-aurora.jpg'); "
        "print('JPEG_OK', p.get_width(), p.get_height())\" 2>&1 || true; "
        "echo '--- monitor1 props ---'; "
        "runuser -u user -- env DISPLAY=:0 XDG_RUNTIME_DIR=/run/user/1000 "
        "xfconf-query -c xfce4-desktop "
        "-p /backdrop/screen0/monitor1/last-image 2>&1 || true",
    ),
    (
        "wallpaper-set-all",
        "for m in monitor0 monitor1; do "
        "for p in last-image last-single-image image-path; do "
        "runuser -u user -- env DISPLAY=:0 XDG_RUNTIME_DIR=/run/user/1000 "
        "xfconf-query -c xfce4-desktop -p /backdrop/screen0/$m/$p "
        "-s /usr/share/xor/wallpapers/xor-royal-aurora.jpg "
        "--create -t string >/dev/null 2>&1 || "
        "runuser -u user -- env DISPLAY=:0 XDG_RUNTIME_DIR=/run/user/1000 "
        "xfconf-query -c xfce4-desktop -p /backdrop/screen0/$m/$p "
        "-s /usr/share/xor/wallpapers/xor-royal-aurora.jpg "
        ">/dev/null 2>&1 || true; done; "
        "runuser -u user -- env DISPLAY=:0 XDG_RUNTIME_DIR=/run/user/1000 "
        "xfconf-query -c xfce4-desktop "
        "-p /backdrop/screen0/$m/image-show -s true "
        "--create -t bool >/dev/null 2>&1 || true; done; "
        "runuser -u user -- env DISPLAY=:0 XDG_RUNTIME_DIR=/run/user/1000 "
        "xfdesktop --reload 2>&1 || true; sleep 3",
    ),
    (
        "wallpaper-after",
        "runuser -u user -- env DISPLAY=:0 XDG_RUNTIME_DIR=/run/user/1000 "
        "xfconf-query -c xfce4-desktop -l -v 2>&1 || true; "
        "runuser -u user -- env DISPLAY=:0 XDG_RUNTIME_DIR=/run/user/1000 "
        "xwininfo -root -tree 2>&1 | grep -Ei 'desktop|xfdesktop' | head -20 || true",
    ),
    (
        "xfdesktop-restart",
        "runuser -u user -- env DISPLAY=:0 XDG_RUNTIME_DIR=/run/user/1000 "
        "xfdesktop --quit 2>&1 || true; sleep 1; "
        "runuser -u user -- env DISPLAY=:0 XDG_RUNTIME_DIR=/run/user/1000 "
        "sh -c 'nohup xfdesktop >/tmp/xfdesktop-restart.log 2>&1 &' "
        "2>&1 || true; sleep 4; "
        "ps -ef | grep '[x]fdesktop' || true; "
        "cat /tmp/xfdesktop-restart.log 2>&1 || true",
    ),
    (
        "wallpaper-png",
        "for m in monitor0 monitor1; do "
        "for p in last-image last-single-image image-path; do "
        "runuser -u user -- env DISPLAY=:0 XDG_RUNTIME_DIR=/run/user/1000 "
        "xfconf-query -c xfce4-desktop -p /backdrop/screen0/$m/$p "
        "-s /usr/share/xor/wallpapers/xor-midnight-x.png "
        "--create -t string >/dev/null 2>&1 || true; done; done; "
        "runuser -u user -- env DISPLAY=:0 XDG_RUNTIME_DIR=/run/user/1000 "
        "xfdesktop --quit 2>&1 || true; sleep 1; "
        "runuser -u user -- env DISPLAY=:0 XDG_RUNTIME_DIR=/run/user/1000 "
        "sh -c 'nohup xfdesktop --enable-debug >/tmp/xfdesktop-png.log 2>&1 &' "
        "2>&1 || true; sleep 5; "
        "cat /tmp/xfdesktop-png.log 2>&1 || true; "
        "ps -ef | grep '[x]fdesktop' || true",
    ),
    (
        "session-env",
        "pid=$(pgrep -u user -n xfce4-session); "
        "test -n \"$pid\" && tr '\\0' '\\n' < /proc/$pid/environ | "
        "grep -E '^(DISPLAY|XDG_CURRENT_DESKTOP|HOME|PATH)=' | sort || true",
    ),
    (
        "xor-runtime",
        "echo '--- binaries ---'; command -v xor-shell xor-shell-autostart "
        "2>&1 || true; ls -l /usr/local/bin/xor-* 2>&1 || true; "
        "echo '--- status ---'; "
        "runuser -u user -- env "
        "PYTHONPATH=/usr/local/lib/xor-desktop:/usr/local/lib/xor-broker "
        "python3 -m xor_shell --status-json 2>&1 || true",
    ),
)


class AgentError(RuntimeError):
    """The guest agent did not answer as expected."""


def connect_socket(path: str, timeout: float) -> socket.socket:
    deadline = time.monotonic() + timeout
    last_error: OSError | None = None
    while time.monotonic() < deadline:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(5.0)
        try:
            sock.connect(path)
            return sock
        except OSError as exc:
            last_error = exc
            sock.close()
            time.sleep(1.0)
    raise AgentError(f"could not connect to {path}: {last_error}")


def read_response(sock: socket.socket, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    data = bytearray()
    while b"\n" not in data:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AgentError("timed out waiting for guest agent response")
        sock.settimeout(remaining)
        try:
            chunk = sock.recv(65536)
        except socket.timeout as exc:
            raise AgentError("timed out waiting for guest agent response") from exc
        if not chunk:
            raise AgentError("guest agent closed the connection")
        data.extend(chunk)
    line, _, _ = bytes(data).partition(b"\n")
    try:
        response = json.loads(line)
    except json.JSONDecodeError as exc:
        raise AgentError(f"invalid guest agent response: {line[:200]!r}") from exc
    if not isinstance(response, dict):
        raise AgentError(f"unexpected guest agent response: {response!r}")
    return response


def request(
    sock: socket.socket, payload: dict[str, Any], timeout: float
) -> dict[str, Any]:
    sock.sendall((json.dumps(payload, separators=(",", ":")) + "\n").encode())
    response = read_response(sock, timeout)
    if "error" in response:
        raise AgentError(f"guest agent error: {response['error']!r}")
    return response


def sync(sock: socket.socket, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    sync_id = 0
    while time.monotonic() < deadline:
        sync_id += 1
        try:
            response = request(
                sock,
                {"execute": "guest-sync", "arguments": {"id": sync_id}},
                timeout=5.0,
            )
        except AgentError:
            time.sleep(1.0)
            continue
        if response.get("return") == sync_id:
            return
        raise AgentError(f"guest-sync returned {response!r}")
    raise AgentError("guest agent did not answer guest-sync")


def run_command(
    sock: socket.socket, command: str, timeout: float = 30.0
) -> tuple[str, str, int]:
    started = request(
        sock,
        {
            "execute": "guest-exec",
            "arguments": {
                "path": "/bin/sh",
                "arg": ["-c", command],
                "capture-output": True,
            },
        },
        timeout=10.0,
    )
    pid = started.get("return", {}).get("pid")
    if not isinstance(pid, int):
        raise AgentError(f"guest-exec returned no pid: {started!r}")

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = request(
            sock,
            {"execute": "guest-exec-status", "arguments": {"pid": pid}},
            timeout=10.0,
        ).get("return", {})
        if status.get("exited"):
            break
        time.sleep(0.25)
    else:
        raise AgentError(f"command did not finish: {command}")

    def decode(key: str) -> str:
        raw = status.get(key, "")
        if not isinstance(raw, str) or not raw:
            return ""
        return base64.b64decode(raw).decode("utf-8", "replace")

    exitcode = status.get("exitcode", -1)
    if not isinstance(exitcode, int):
        exitcode = -1
    return decode("out-data"), decode("err-data"), exitcode


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("socket", help="QEMU guest agent Unix socket")
    parser.add_argument(
        "output", nargs="?", help="directory for diagnostic text files")
    parser.add_argument(
        "--connect-timeout",
        type=float,
        default=120.0,
        help="seconds to wait for the agent socket (default: 120)",
    )
    parser.add_argument(
        "--exec", dest="exec_command", metavar="COMMAND",
        help="run one shell command in the guest instead of the diagnostic set",
    )
    parser.add_argument(
        "--exec-timeout",
        type=float,
        default=180.0,
        help="seconds to wait for --exec to finish (default: 180)",
    )
    args = parser.parse_args()
    if args.exec_command is None and not args.output:
        parser.error("output is required unless --exec is used")

    if args.exec_command is not None:
        try:
            sock = connect_socket(args.socket, args.connect_timeout)
            try:
                sync(sock, args.connect_timeout)
                stdout, stderr, exitcode = run_command(
                    sock, args.exec_command, args.exec_timeout)
            finally:
                sock.close()
        except (AgentError, OSError) as exc:
            print(f"qga-exec: {exc}", file=sys.stderr)
            return 1
        sys.stdout.write(stdout)
        sys.stderr.write(stderr)
        print(f"qga_exitcode={exitcode}", file=sys.stderr)
        return 0 if exitcode == 0 else 1

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    summary: list[str] = []

    try:
        sock = connect_socket(args.socket, args.connect_timeout)
        try:
            sync(sock, args.connect_timeout)
            for name, command in COMMANDS:
                try:
                    stdout, stderr, exitcode = run_command(sock, command)
                except AgentError as exc:
                    text = f"command failed: {exc}\n"
                    (output / f"{name}.txt").write_text(text, encoding="utf-8")
                    summary.append(f"{name}: ERROR: {exc}")
                    continue
                text = (
                    f"$ {command}\n"
                    f"exitcode={exitcode}\n"
                    f"--- stdout ---\n{stdout}"
                    f"--- stderr ---\n{stderr}"
                )
                (output / f"{name}.txt").write_text(text, encoding="utf-8")
                summary.append(f"{name}: exit={exitcode} bytes={len(text)}")
        finally:
            sock.close()
    except (AgentError, OSError) as exc:
        (output / "qga-error.txt").write_text(f"{exc}\n", encoding="utf-8")
        print(f"qga-query: {exc}", file=sys.stderr)
        return 1

    (output / "qga-summary.txt").write_text(
        "\n".join(summary) + "\n", encoding="utf-8"
    )
    print("\n".join(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
