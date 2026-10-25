"""End-to-end broker tests: confirmation binding, audit, manifests, IPC."""
import io
import json
import os
import stat
import sys
import tempfile
import threading
import unittest
from datetime import timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from xor_broker import executor  # noqa: E402
from xor_broker.audit import AuditLog, read_records, verify_chain  # noqa: E402
from xor_broker.broker import Broker  # noqa: E402
from xor_broker.confirm import (  # noqa: E402
    ApprovalToken, AutoDenyConfirmation, CliConfirmation, ConfirmationManager,
    ScriptedConfirmation,
)
from xor_broker.manifest import ManifestError, parse_manifest  # noqa: E402
from xor_broker.models import (  # noqa: E402
    ActionRequest, Operation, OutcomeStatus, utcnow,
)
from xor_broker.policy import PolicyEngine  # noqa: E402


def build(root, provider, files):
    """ files: {name: content}; grants read on granted.txt, write on out.txt """
    paths = {}
    for name, content in files.items():
        p = os.path.join(root, name)
        with open(p, "w") as fh:
            fh.write(content)
        paths[name] = p
    manifest = {"capabilities": [
        {"capability_id": "r1", "tool": "tool-a", "operation": "read_file",
         "resource": {"type": "file", "path": paths["granted.txt"]}, "rights": ["read"]},
        {"capability_id": "w1", "tool": "tool-a", "operation": "write_file",
         "resource": {"type": "file", "path": paths["out.txt"]}, "rights": ["write"]},
    ]}
    broker = Broker(
        PolicyEngine(parse_manifest(manifest)),
        ConfirmationManager(provider),
        AuditLog(os.path.join(root, "audit.jsonl")),
    )
    return broker, paths, os.path.join(root, "audit.jsonl")


class BrokerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, provider):
        return build(self.root, provider,
                     {"granted.txt": "hello granted", "secret.txt": "TOP-SECRET-CONTENT",
                      "out.txt": "old"})


class TestConfirmationFlow(BrokerCase):
    def test_write_executes_only_after_approval(self):
        broker, paths, _ = self.make(ScriptedConfirmation(default=True))
        out = broker.handle(ActionRequest("tool-a", Operation.WRITE_FILE,
                                          paths["out.txt"], b"new content"))
        self.assertEqual(out.status, OutcomeStatus.EXECUTED)
        with open(paths["out.txt"]) as fh:
            self.assertEqual(fh.read(), "new content")

    def test_write_denied_by_user_does_not_execute(self):
        broker, paths, _ = self.make(ScriptedConfirmation(default=False))
        out = broker.handle(ActionRequest("tool-a", Operation.WRITE_FILE,
                                          paths["out.txt"], b"attacker"))
        self.assertEqual(out.status, OutcomeStatus.DENIED_USER)
        with open(paths["out.txt"]) as fh:
            self.assertEqual(fh.read(), "old")

    def test_auto_deny_provider_blocks_all_writes(self):
        broker, paths, _ = self.make(AutoDenyConfirmation())
        out = broker.handle(ActionRequest("tool-a", Operation.WRITE_FILE,
                                          paths["out.txt"], b"x"))
        self.assertEqual(out.status, OutcomeStatus.DENIED_USER)

    def test_provider_exception_denies(self):
        def boom(prompt):
            raise RuntimeError("UI crashed")
        broker, paths, _ = self.make(ScriptedConfirmation(answers=boom))
        out = broker.handle(ActionRequest("tool-a", Operation.WRITE_FILE,
                                          paths["out.txt"], b"x"))
        self.assertEqual(out.status, OutcomeStatus.DENIED_USER)
        with open(paths["out.txt"]) as fh:
            self.assertEqual(fh.read(), "old")

    def test_token_for_one_action_cannot_authorise_another(self):
        broker, paths, _ = self.make(ScriptedConfirmation(default=True))
        cap = broker.policy.find("w1")
        req_a = ActionRequest("tool-a", Operation.WRITE_FILE, paths["out.txt"], b"A")
        req_b = ActionRequest("tool-a", Operation.WRITE_FILE, paths["out.txt"], b"B")
        from xor_broker.paths import canonicalize
        canon = canonicalize(paths["out.txt"])
        token = ApprovalToken("p1", req_a.action_digest(canon),
                              utcnow() + timedelta(seconds=60))
        out = executor.execute(req_b, cap, canon, utcnow(), token)
        self.assertEqual(out.reason.value, "digest_mismatch")
        with open(paths["out.txt"]) as fh:
            self.assertEqual(fh.read(), "old")

    def test_expired_and_replayed_tokens_rejected(self):
        broker, paths, _ = self.make(ScriptedConfirmation(default=True))
        cap = broker.policy.find("w1")
        from xor_broker.paths import canonicalize
        canon = canonicalize(paths["out.txt"])
        req = ActionRequest("tool-a", Operation.WRITE_FILE, paths["out.txt"], b"A")

        stale = ApprovalToken("p2", req.action_digest(canon),
                              utcnow() - timedelta(seconds=1))
        out = executor.execute(req, cap, canon, utcnow(), stale)
        self.assertEqual(out.reason.value, "stale_confirmation")

        good = ApprovalToken("p3", req.action_digest(canon),
                             utcnow() + timedelta(seconds=60))
        out = executor.execute(req, cap, canon, utcnow(), good)
        self.assertEqual(out.status, OutcomeStatus.EXECUTED)
        out2 = executor.execute(req, cap, canon, utcnow(), good)
        self.assertEqual(out2.reason.value, "stale_confirmation")

    def test_cli_confirmation_default_deny(self):
        from xor_broker.confirm import ConfirmationPrompt
        prompt = ConfirmationPrompt("p", "tool-a", "write_file", "/x", "desc", "prev")
        deny = CliConfirmation(instream=io.StringIO("\n"), outstream=io.StringIO())
        self.assertFalse(deny.confirm(prompt))
        eof = CliConfirmation(instream=io.StringIO(""), outstream=io.StringIO())
        self.assertFalse(eof.confirm(prompt))
        yes = CliConfirmation(instream=io.StringIO("approve\n"), outstream=io.StringIO())
        self.assertTrue(yes.confirm(prompt))
        sneaky = CliConfirmation(instream=io.StringIO("yes\n"), outstream=io.StringIO())
        self.assertFalse(sneaky.confirm(prompt))  # only exact word 'approve'


class TestAudit(BrokerCase):
    def test_every_request_audited_and_content_never_logged(self):
        broker, paths, audit_path = self.make(ScriptedConfirmation(default=True))
        broker.handle(ActionRequest("tool-a", Operation.READ_FILE, paths["granted.txt"]))
        broker.handle(ActionRequest("tool-a", Operation.READ_FILE, paths["secret.txt"]))
        broker.handle(ActionRequest("tool-a", Operation.WRITE_FILE, paths["out.txt"],
                                    b"WRITE-PAYLOAD-MARKER"))
        broker.handle(ActionRequest("tool-a", Operation.NETWORK_SEND,
                                    "https://x.invalid", b"EXFIL-MARKER"))
        records = list(read_records(audit_path))
        self.assertEqual(len(records), 4)
        with open(audit_path) as fh:
            raw = fh.read()
        for marker in ("TOP-SECRET-CONTENT", "hello granted",
                       "WRITE-PAYLOAD-MARKER", "EXFIL-MARKER"):
            self.assertNotIn(marker, raw)
        ok, n, _ = verify_chain(audit_path)
        self.assertTrue(ok)
        self.assertEqual(n, 4)
        mode = stat.S_IMODE(os.stat(audit_path).st_mode)
        self.assertEqual(mode, 0o600)

    def test_tampering_breaks_chain(self):
        broker, paths, audit_path = self.make(ScriptedConfirmation(default=True))
        broker.handle(ActionRequest("tool-a", Operation.READ_FILE, paths["granted.txt"]))
        broker.handle(ActionRequest("tool-a", Operation.READ_FILE, paths["secret.txt"]))
        with open(audit_path) as fh:
            lines = fh.readlines()
        rec = json.loads(lines[0])
        rec["tool"] = "rewritten-by-attacker"  # rewrite history
        lines[0] = json.dumps(rec, sort_keys=True, separators=(",", ":")) + "\n"
        with open(audit_path, "w") as fh:
            fh.writelines(lines)
        ok, _, bad = verify_chain(audit_path)
        self.assertFalse(ok)
        self.assertEqual(bad, 1)


class TestManifestValidation(unittest.TestCase):
    def test_rejects_granting_unsupported_operations(self):
        bad = {"capabilities": [{"capability_id": "x", "tool": "t",
                                 "operation": "network_send",
                                 "resource": {"type": "file", "path": "/tmp/a"},
                                 "rights": ["read"]}]}
        with self.assertRaises(ManifestError):
            parse_manifest(bad)

    def test_rejects_malformed_entries(self):
        cases = [
            {"capabilities": "nope"},
            {"capabilities": [{"tool": "t"}]},
            {"capabilities": [{"capability_id": "a", "tool": "t",
                               "operation": "teleport",
                               "resource": {"type": "file", "path": "/x"},
                               "rights": ["read"]}]},
            {"capabilities": [{"capability_id": "a", "tool": "t",
                               "operation": "read_file",
                               "resource": {"type": "universe", "path": "/x"},
                               "rights": ["read"]}]},
            {"capabilities": [{"capability_id": "a", "tool": "t",
                               "operation": "read_file",
                               "resource": {"type": "file", "path": "/x"},
                               "rights": ["everything"]}]},
            {"capabilities": [{"capability_id": "a", "tool": "t",
                               "operation": "read_file",
                               "resource": {"type": "file", "path": "/x"},
                               "rights": ["read"], "expires_at": "tomorrow-ish"}]},
        ]
        for case in cases:
            with self.assertRaises(ManifestError, msg=str(case)):
                parse_manifest(case)

    def test_rejects_duplicate_ids(self):
        entry = {"capability_id": "dup", "tool": "t", "operation": "read_file",
                 "resource": {"type": "file", "path": "/x"}, "rights": ["read"]}
        with self.assertRaises(ManifestError):
            parse_manifest({"capabilities": [entry, dict(entry)]})


class TestSocketRoundTrip(BrokerCase):
    def test_client_server_and_denials(self):
        from xor_broker.server import BrokerClient, BrokerError, serve
        broker, paths, _ = self.make(ScriptedConfirmation(default=True))
        sock = os.path.join(self.root, "b.sock")
        thread = threading.Thread(target=serve, args=(broker, sock), daemon=True)
        thread.start()
        for _ in range(100):
            if os.path.exists(sock):
                break
            threading.Event().wait(0.05)
        client = BrokerClient("tool-a", sock)
        self.assertEqual(client.read_file(paths["granted.txt"]), b"hello granted")
        with self.assertRaises(BrokerError):
            client.read_file(paths["secret.txt"])
        resp = client.write_file(paths["out.txt"], b"via socket")
        self.assertEqual(resp["status"], "executed")
        self.assertEqual(stat.S_IMODE(os.stat(sock).st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
