"""``xor-broker`` command line: demo, daemon, requests, audit tools."""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import tempfile

from . import __version__
from .audit import AuditLog, read_records, verify_chain
from .broker import Broker
from .confirm import CliConfirmation, ConfirmationManager
from .config import default_audit_path, default_manifest_path, default_socket_path
from .manifest import load_manifest
from .models import ActionRequest, Operation
from .policy import PolicyEngine


def build_broker(manifest_path: str, audit_path: str, provider=None) -> Broker:
    capabilities = load_manifest(manifest_path)
    audit = AuditLog(audit_path)
    # max_uses must survive a daemon restart / a one-shot CLI request.
    # The audit log is the durable evidence of successful executions, so
    # derive the counters from it instead of keeping them only in memory.
    counts = audit.executed_counts_by_capability()
    for capability in capabilities:
        capability.uses = counts.get(capability.capability_id, 0)
    return Broker(
        PolicyEngine(capabilities),
        ConfirmationManager(provider or CliConfirmation()),
        audit,
    )


def cmd_demo(args) -> int:
    """Self-contained demonstration in a temp sandbox (nothing outside
    the sandbox is touched).  Shows: allowed read, denied sibling read,
    confirmed write, denied network send, and the audit trail."""
    root = tempfile.mkdtemp(prefix="xor-demo-") if not args.keep else args.keep
    os.makedirs(root, exist_ok=True)
    docs = os.path.join(root, "Documents")
    os.makedirs(docs, exist_ok=True)
    granted = os.path.join(docs, "notes.txt")
    secret = os.path.join(docs, "secret.txt")
    out_file = os.path.join(docs, "summary.txt")
    with open(granted, "w") as fh:
        fh.write("Meeting notes: ship Milestone 1.\n")
    with open(secret, "w") as fh:
        fh.write("TOKEN=hunter2 (must never be read by the demo tool)\n")

    manifest = {
        "capabilities": [
            {"capability_id": "demo-read-notes", "tool": "demo-tool",
             "operation": "read_file",
             "resource": {"type": "file", "path": granted}, "rights": ["read"]},
            {"capability_id": "demo-write-summary", "tool": "demo-tool",
             "operation": "write_file",
             "resource": {"type": "file", "path": out_file}, "rights": ["write"]},
        ]
    }
    manifest_path = os.path.join(root, "manifest.json")
    with open(manifest_path, "w") as fh:
        json.dump(manifest, fh, indent=2)
    audit_path = os.path.join(root, "audit.jsonl")
    broker = build_broker(manifest_path, audit_path)

    def show(label, outcome):
        extra = f" -> {outcome.data.decode().strip()!r}" if outcome.data else ""
        print(f"[{label}] {outcome.status.value} ({outcome.reason.value}){extra}")

    print(f"Demo sandbox: {root}\n")
    show("read granted file   ",
         broker.handle(ActionRequest("demo-tool", Operation.READ_FILE, granted)))
    show("read SIBLING secret ",
         broker.handle(ActionRequest("demo-tool", Operation.READ_FILE, secret)))
    show("read via traversal  ",
         broker.handle(ActionRequest("demo-tool", Operation.READ_FILE,
                                     os.path.join(docs, "..", "Documents", "secret.txt"))))
    show("write granted file  ",
         broker.handle(ActionRequest(
             "demo-tool", Operation.WRITE_FILE, out_file,
             content=b"Summary: Milestone 1 on track.\n",
             description="Write one-line meeting summary")))
    show("network send        ",
         broker.handle(ActionRequest("demo-tool", Operation.NETWORK_SEND,
                                     "https://example.invalid/exfil",
                                     content=b"Meeting notes: ship Milestone 1.")))

    print("\nAudit log (note: digests, no file contents):")
    for rec in read_records(audit_path):
        print(f"  #{rec['seq']} {rec['operation']:<12} decision={rec['decision']:<5} "
              f"result={rec['result']:<16} reason={rec['reason']}")
    ok, n, _ = verify_chain(audit_path)
    print(f"\nAudit hash chain valid: {ok} ({n} records)")
    return 0 if ok else 1


def _provider(name: str):
    if name == "gtk":
        from .confirm_gtk import GtkConfirmation
        return GtkConfirmation()
    if name == "deny":
        from .confirm import AutoDenyConfirmation
        return AutoDenyConfirmation()
    return CliConfirmation()


def cmd_init(args) -> int:
    """Create ~/XOR-Demo files and a starter manifest, if absent.
    Grants demo-reader read on ONE file and demo-writer write on ONE
    file - nothing else.  Existing files are never overwritten."""
    home = os.path.expanduser("~")
    demo_dir = os.path.join(home, "XOR-Demo")
    shared = os.path.join(demo_dir, "shared")
    os.makedirs(shared, exist_ok=True)
    notes = os.path.join(demo_dir, "notes.txt")
    summary = os.path.join(demo_dir, "summary.txt")
    if not os.path.exists(notes):
        with open(notes, "w") as fh:
            fh.write("XOR demo notes.\nThe demo-reader tool may read this file only.\n")
    if not os.path.exists(os.path.join(shared, "readme.txt")):
        with open(os.path.join(shared, "readme.txt"), "w") as fh:
            fh.write("Files in shared/ can be listed by demo-reader.\n")
    manifest_path = args.manifest
    manifest_dir = os.path.dirname(os.path.abspath(manifest_path))
    default_dir = os.path.dirname(os.path.abspath(default_manifest_path()))
    existed = os.path.isdir(manifest_dir)
    os.makedirs(manifest_dir, mode=0o700, exist_ok=True)
    if not existed or manifest_dir == default_dir:
        # Harden the default broker directory, and any directory we just
        # created.  Never chmod an arbitrary pre-existing parent (a custom
        # path such as /tmp/manifest.json must not turn /tmp into 0700).
        try:
            os.chmod(manifest_dir, 0o700)
        except OSError:
            pass
    if os.path.lexists(manifest_path):
        # Do not overwrite an existing manifest, but do not leave a
        # user-controlled grant file world-readable either.  Open with
        # O_NOFOLLOW and chmod the opened FD so a symlink can never redirect
        # the chmod onto another file.
        try:
            fd = os.open(
                manifest_path,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
            )
        except OSError as exc:
            print(f"Refusing unsafe manifest path {manifest_path}: {exc}",
                  file=sys.stderr)
            return 2
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                print(f"Refusing manifest path that is not a regular file: "
                      f"{manifest_path}", file=sys.stderr)
                return 2
            try:
                os.fchmod(fd, 0o600)
            except OSError as exc:
                print(f"Cannot secure existing manifest {manifest_path}: {exc}",
                      file=sys.stderr)
                return 2
        finally:
            os.close(fd)
        print(f"Manifest already exists, leaving contents untouched: {manifest_path}")
        return 0
    manifest = {"capabilities": [
        {"capability_id": "demo-reader-notes", "tool": "demo-reader",
         "operation": "read_file", "resource": {"type": "file", "path": notes},
         "rights": ["read"]},
        {"capability_id": "demo-reader-list-shared", "tool": "demo-reader",
         "operation": "list_dir", "resource": {"type": "dir", "path": shared},
         "rights": ["read"]},
        {"capability_id": "demo-writer-summary", "tool": "demo-writer",
         "operation": "write_file", "resource": {"type": "file", "path": summary},
         "rights": ["write"]},
    ]}
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open(manifest_path, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    print(f"Wrote starter manifest: {manifest_path}")
    print(f"Demo files in: {demo_dir}")
    return 0


def cmd_serve(args) -> int:
    from .server import serve

    broker = build_broker(args.manifest, args.audit, provider=_provider(args.confirm))
    print(f"xor-broker listening on {args.socket} (confirm={args.confirm}, Ctrl-C to stop)")
    try:
        serve(broker, args.socket)
    except KeyboardInterrupt:
        pass
    return 0


def cmd_request(args) -> int:
    broker = build_broker(args.manifest, args.audit)
    content = args.content.encode() if args.content is not None else None
    outcome = broker.handle(ActionRequest(
        args.tool, Operation(args.operation), args.path,
        content=content, description=args.description))
    print(f"status={outcome.status.value} reason={outcome.reason.value} "
          f"capability={outcome.capability_id}")
    if outcome.data is not None:
        sys.stdout.write(outcome.data.decode("utf-8", errors="replace"))
    if outcome.listing is not None:
        print("\n".join(outcome.listing))
    return 0 if outcome.status.value == "executed" else 3


def cmd_audit(args) -> int:
    if args.audit_cmd == "verify":
        ok, n, bad = verify_chain(args.audit)
        print(f"chain valid: {ok}; records: {n}" + (f"; first bad seq: {bad}" if bad else ""))
        return 0 if ok else 1
    for rec in read_records(args.audit):
        print(f"#{rec['seq']:<4} {rec['timestamp']} tool={rec['tool']} "
              f"op={rec['operation']} resource={rec['resource']} "
              f"decision={rec['decision']} confirm={rec['confirmation']} "
              f"result={rec['result']} reason={rec['reason']}")
    return 0


def cmd_grants(args) -> int:
    for cap in load_manifest(args.manifest):
        print(f"{cap.capability_id}: tool={cap.tool} op={cap.operation.value} "
              f"{cap.resource.kind.value}={cap.resource.path} rights={sorted(cap.rights)} "
              f"expires={cap.expires_at} max_uses={cap.max_uses}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="xor-broker", description="XOR OS permission broker (default-deny)")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--manifest", default=default_manifest_path())
    parser.add_argument("--audit", default=default_audit_path())
    sub = parser.add_subparsers(dest="cmd", required=True)

    demo = sub.add_parser("demo", help="run a self-contained demonstration")
    demo.add_argument("--keep", metavar="DIR", help="use DIR as sandbox instead of a temp dir")
    demo.set_defaults(fn=cmd_demo)

    init_p = sub.add_parser("init", help="create demo files + starter manifest")
    init_p.set_defaults(fn=cmd_init)

    serve_p = sub.add_parser("serve", help="run the Unix-socket daemon")
    serve_p.add_argument("--socket", default=default_socket_path())
    serve_p.add_argument("--confirm", default="cli", choices=["cli", "gtk", "deny"],
                         help="write-confirmation UI (gtk on the desktop)")
    serve_p.set_defaults(fn=cmd_serve)

    req = sub.add_parser("request", help="make one broker request")
    req.add_argument("--tool", required=True)
    req.add_argument("--operation", required=True,
                     choices=[op.value for op in Operation])
    req.add_argument("--path", required=True)
    req.add_argument("--content")
    req.add_argument("--description", default="")
    req.set_defaults(fn=cmd_request)

    audit_p = sub.add_parser("audit", help="inspect the audit log")
    audit_p.add_argument("audit_cmd", choices=["show", "verify"])
    audit_p.set_defaults(fn=cmd_audit)

    grants_p = sub.add_parser("grants", help="list capabilities in the manifest")
    grants_p.set_defaults(fn=cmd_grants)

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
