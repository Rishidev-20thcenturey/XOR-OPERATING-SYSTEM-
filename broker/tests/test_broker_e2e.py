"""End-to-end broker tests: confirmation binding, audit, manifests, IPC."""
import hashlib
import io
import json
import multiprocessing
import os
import stat
import sys
import tempfile
import threading
import time
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from xor_broker import executor  # noqa: E402
from xor_broker.audit import (  # noqa: E402
    AuditIntegrityError, AuditLog, read_records, verify_chain,
)
from xor_broker.broker import Broker  # noqa: E402
from xor_broker.cli import build_broker, cmd_init  # noqa: E402
from xor_broker.confirm import (  # noqa: E402
    ApprovalToken, AutoDenyConfirmation, CliConfirmation, ConfirmationManager,
    ScriptedConfirmation,
)
from xor_broker.manifest import (  # noqa: E402
    ManifestError, grant_for_selected_file, load_manifest, parse_manifest,
)
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


def concurrent_read_worker(manifest_path, audit_path, granted, start_path,
                           result_path):
    """Child-process helper for the cross-process max_uses test."""
    broker = build_broker(
        manifest_path, audit_path, provider=ScriptedConfirmation(default=True))
    while not os.path.exists(start_path):
        time.sleep(0.01)
    real_execute = executor.execute

    def slow_execute(*args, **kwargs):
        time.sleep(0.2)
        return real_execute(*args, **kwargs)

    with patch("xor_broker.executor.execute", side_effect=slow_execute):
        outcome = broker.handle(
            ActionRequest("tool-a", Operation.READ_FILE, granted))
    with open(result_path, "w") as fh:
        fh.write(f"{outcome.status.value}\n{outcome.reason.value}\n")


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

    def test_write_creates_private_file(self):
        broker, paths, _ = self.make(ScriptedConfirmation(default=True))
        os.unlink(paths["out.txt"])
        out = broker.handle(ActionRequest("tool-a", Operation.WRITE_FILE,
                                          paths["out.txt"], b"new content"))
        self.assertEqual(out.status, OutcomeStatus.EXECUTED)
        self.assertEqual(stat.S_IMODE(os.stat(paths["out.txt"]).st_mode), 0o600)

    def test_write_denied_by_user_does_not_execute(self):
        broker, paths, _ = self.make(ScriptedConfirmation(default=False))
        out = broker.handle(ActionRequest("tool-a", Operation.WRITE_FILE,
                                          paths["out.txt"], b"attacker"))
        self.assertEqual(out.status, OutcomeStatus.DENIED_USER)
        with open(paths["out.txt"]) as fh:
            self.assertEqual(fh.read(), "old")

    def test_write_syscall_failure_is_reported(self):
        broker, paths, _ = self.make(ScriptedConfirmation(default=True))
        target = os.stat(paths["out.txt"])
        real_write = os.write

        def fail_target_write(fd, data):
            opened = os.fstat(fd)
            if (opened.st_dev, opened.st_ino) == (target.st_dev, target.st_ino):
                raise OSError("simulated disk full")
            return real_write(fd, data)

        with patch("xor_broker.executor.os.write", side_effect=fail_target_write):
            out = broker.handle(ActionRequest(
                "tool-a", Operation.WRITE_FILE, paths["out.txt"], b"new content"))

        self.assertEqual(out.status, OutcomeStatus.FAILED)
        self.assertEqual(out.reason.value, "execution_error")
        self.assertIn("disk full", out.error)

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


class TestSelectedDirectoryGrant(BrokerCase):
    def test_list_dir_grant_actually_lists_selected_directory(self):
        selected = os.path.join(self.root, "selected")
        os.makedirs(selected)
        for name in ("b.txt", "a.txt"):
            with open(os.path.join(selected, name), "w") as fh:
                fh.write(name)
        cap = grant_for_selected_file(
            "tool-a", Operation.LIST_DIR, selected, "selected-dir")
        broker = Broker(
            PolicyEngine([cap]),
            ConfirmationManager(AutoDenyConfirmation()),
            AuditLog(os.path.join(self.root, "audit.jsonl")),
        )
        out = broker.handle(ActionRequest("tool-a", Operation.LIST_DIR, selected))
        self.assertEqual(out.status, OutcomeStatus.EXECUTED)
        self.assertEqual(out.listing, ["a.txt", "b.txt"])

    def test_replaced_selected_directory_is_rejected(self):
        selected = os.path.join(self.root, "selected")
        original = os.path.join(self.root, "selected-original")
        os.makedirs(selected)
        with open(os.path.join(selected, "approved.txt"), "w") as fh:
            fh.write("approved")
        cap = grant_for_selected_file(
            "tool-a", Operation.LIST_DIR, selected, "selected-dir")
        broker = Broker(
            PolicyEngine([cap]),
            ConfirmationManager(AutoDenyConfirmation()),
            AuditLog(os.path.join(self.root, "audit.jsonl")),
        )

        os.rename(selected, original)
        os.makedirs(selected)
        with open(os.path.join(selected, "attacker.txt"), "w") as fh:
            fh.write("replacement")

        out = broker.handle(ActionRequest("tool-a", Operation.LIST_DIR, selected))
        self.assertEqual(out.status, OutcomeStatus.DENIED_POLICY)
        self.assertEqual(out.reason.value, "path_escape")
        self.assertIsNone(out.listing)


class TestPinnedWriteSafety(BrokerCase):
    def _pinned_broker(self, selected):
        cap = grant_for_selected_file(
            "tool-a", Operation.WRITE_FILE, selected, "selected-write")
        broker = Broker(
            PolicyEngine([cap]),
            ConfirmationManager(ScriptedConfirmation(default=True)),
            AuditLog(os.path.join(self.root, "audit.jsonl")),
        )
        return broker

    def test_pinned_file_normal_write_succeeds(self):
        selected = os.path.join(self.root, "selected.txt")
        with open(selected, "w") as fh:
            fh.write("original")
        broker = self._pinned_broker(selected)

        out = broker.handle(ActionRequest(
            "tool-a", Operation.WRITE_FILE, selected, b"approved replacement"))

        self.assertEqual(out.status, OutcomeStatus.EXECUTED)
        with open(selected) as fh:
            self.assertEqual(fh.read(), "approved replacement")

    def test_replaced_pinned_file_is_not_truncated(self):
        selected = os.path.join(self.root, "selected.txt")
        original = os.path.join(self.root, "selected-original.txt")
        with open(selected, "w") as fh:
            fh.write("original approved content")
        broker = self._pinned_broker(selected)

        os.rename(selected, original)
        with open(selected, "w") as fh:
            fh.write("replacement must survive")

        out = broker.handle(ActionRequest(
            "tool-a", Operation.WRITE_FILE, selected, b"attacker payload"))

        self.assertEqual(out.status, OutcomeStatus.DENIED_POLICY)
        self.assertEqual(out.reason.value, "path_escape")
        with open(selected) as fh:
            self.assertEqual(fh.read(), "replacement must survive")
        with open(original) as fh:
            self.assertEqual(fh.read(), "original approved content")

    def test_deleted_pinned_file_is_not_recreated(self):
        selected = os.path.join(self.root, "selected.txt")
        with open(selected, "w") as fh:
            fh.write("original")
        broker = self._pinned_broker(selected)
        os.unlink(selected)

        out = broker.handle(ActionRequest(
            "tool-a", Operation.WRITE_FILE, selected, b"must not create"))

        self.assertEqual(out.status, OutcomeStatus.FAILED)
        self.assertEqual(out.reason.value, "execution_error")
        self.assertFalse(os.path.exists(selected))


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

    def test_rejects_file_scoped_list_dir_grant(self):
        bad = {"capabilities": [{
            "capability_id": "x", "tool": "t", "operation": "list_dir",
            "resource": {"type": "file", "path": "/tmp/a"},
            "rights": ["read"],
        }]}
        with self.assertRaises(ManifestError):
            parse_manifest(bad)

    def test_load_manifest_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as root:
            target = os.path.join(root, "real.json")
            with open(target, "w") as fh:
                json.dump({"capabilities": []}, fh)
            link = os.path.join(root, "manifest.json")
            os.symlink(target, link)
            with self.assertRaises(ManifestError):
                load_manifest(link)

    def test_load_manifest_rejects_non_regular_file(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(ManifestError):
                load_manifest(root)

    def test_build_broker_rejects_symlinked_manifest(self):
        with tempfile.TemporaryDirectory() as root:
            target = os.path.join(root, "real.json")
            with open(target, "w") as fh:
                json.dump({"capabilities": []}, fh)
            link = os.path.join(root, "manifest.json")
            os.symlink(target, link)
            with self.assertRaises(ManifestError):
                build_broker(link, os.path.join(root, "audit.jsonl"))

    def test_rejects_duplicate_ids(self):
        entry = {"capability_id": "dup", "tool": "t", "operation": "read_file",
                 "resource": {"type": "file", "path": "/x"}, "rights": ["read"]}
        with self.assertRaises(ManifestError):
            parse_manifest({"capabilities": [entry, dict(entry)]})


class TestDurableUseLimits(BrokerCase):
    def test_max_uses_survives_broker_rebuild(self):
        granted = os.path.join(self.root, "granted.txt")
        audit_path = os.path.join(self.root, "audit.jsonl")
        manifest_path = os.path.join(self.root, "manifest.json")
        with open(granted, "w") as fh:
            fh.write("one read only")
        manifest = {"capabilities": [{
            "capability_id": "once", "tool": "tool-a",
            "operation": "read_file",
            "resource": {"type": "file", "path": granted},
            "rights": ["read"], "max_uses": 1,
        }]}
        with open(manifest_path, "w") as fh:
            json.dump(manifest, fh)

        first = build_broker(manifest_path, audit_path,
                             provider=ScriptedConfirmation(default=True))
        self.assertEqual(
            first.handle(ActionRequest("tool-a", Operation.READ_FILE, granted)).status,
            OutcomeStatus.EXECUTED)

        # A fresh process (as the CLI/daemon restart would do) must see
        # the execution already recorded in the audit log.
        second = build_broker(manifest_path, audit_path,
                              provider=ScriptedConfirmation(default=True))
        out = second.handle(ActionRequest("tool-a", Operation.READ_FILE, granted))
        self.assertEqual(out.status, OutcomeStatus.DENIED_POLICY)
        self.assertEqual(out.reason.value, "capability_exhausted")


class TestCrossProcessUseLimits(BrokerCase):
    def _single_use_setup(self):
        granted = os.path.join(self.root, "granted.txt")
        audit_path = os.path.join(self.root, "audit.jsonl")
        manifest_path = os.path.join(self.root, "manifest.json")
        with open(granted, "w") as fh:
            fh.write("only one execution")
        manifest = {"capabilities": [{
            "capability_id": "once", "tool": "tool-a",
            "operation": "read_file",
            "resource": {"type": "file", "path": granted},
            "rights": ["read"], "max_uses": 1,
        }]}
        with open(manifest_path, "w") as fh:
            json.dump(manifest, fh)
        return granted, manifest_path, audit_path

    def test_concurrent_requests_cannot_exceed_max_uses(self):
        granted, manifest_path, audit_path = self._single_use_setup()
        brokers = [
            build_broker(manifest_path, audit_path,
                         provider=ScriptedConfirmation(default=True))
            for _ in range(2)
        ]
        barrier = threading.Barrier(2)
        outcomes = []
        real_execute = executor.execute

        def slow_execute(*args, **kwargs):
            # Widen the window between the policy decision and the audit
            # append so an unserialised implementation would race.
            time.sleep(0.2)
            return real_execute(*args, **kwargs)

        def run(broker):
            barrier.wait()
            outcomes.append(
                broker.handle(ActionRequest("tool-a", Operation.READ_FILE, granted)))

        with patch("xor_broker.executor.execute", side_effect=slow_execute):
            threads = [threading.Thread(target=run, args=(broker,), daemon=True)
                       for broker in brokers]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(5)
                self.assertFalse(thread.is_alive(), "broker request deadlocked")

        statuses = sorted(outcome.status.value for outcome in outcomes)
        self.assertEqual(statuses, ["denied_policy", "executed"])
        denied = next(o for o in outcomes if o.status is OutcomeStatus.DENIED_POLICY)
        self.assertEqual(denied.reason.value, "capability_exhausted")
        records = list(read_records(audit_path))
        self.assertEqual(len(records), 2)
        self.assertEqual(sum(r["result"] == "executed" for r in records), 1)
        self.assertTrue(verify_chain(audit_path)[0])

    def test_concurrent_processes_cannot_exceed_max_uses(self):
        try:
            ctx = multiprocessing.get_context("fork")
        except ValueError:
            self.skipTest("fork start method unavailable")
        granted, manifest_path, audit_path = self._single_use_setup()
        start_path = os.path.join(self.root, "start")
        result_paths = [os.path.join(self.root, f"result-{i}")
                        for i in range(2)]
        processes = [
            ctx.Process(target=concurrent_read_worker,
                        args=(manifest_path, audit_path, granted, start_path,
                              result_path))
            for result_path in result_paths
        ]
        for process in processes:
            process.start()
        with open(start_path, "w") as fh:
            fh.write("go")
        for process in processes:
            process.join(10)
            self.assertEqual(process.exitcode, 0, "worker process failed")

        outcomes = []
        for result_path in result_paths:
            with open(result_path) as fh:
                status, reason = fh.read().splitlines()[:2]
            outcomes.append((status, reason))
        self.assertEqual(sorted(status for status, _ in outcomes),
                         ["denied_policy", "executed"])
        self.assertIn(("denied_policy", "capability_exhausted"), outcomes)
        self.assertEqual(
            sum(r["result"] == "executed" for r in read_records(audit_path)), 1)

    def test_daemon_and_cli_share_use_limit(self):
        from xor_broker.server import BrokerClient, serve

        granted, manifest_path, audit_path = self._single_use_setup()
        # Build the CLI-side broker first so it starts with a stale zero
        # count; the daemon then consumes the only use.
        cli_broker = build_broker(
            manifest_path, audit_path, provider=ScriptedConfirmation(default=True))
        daemon_broker = build_broker(
            manifest_path, audit_path, provider=ScriptedConfirmation(default=True))
        sock = os.path.join(self.root, "broker.sock")
        thread = threading.Thread(target=serve, args=(daemon_broker, sock), daemon=True)
        thread.start()
        for _ in range(100):
            if os.path.exists(sock):
                break
            threading.Event().wait(0.05)

        self.assertEqual(BrokerClient("tool-a", sock).read_file(granted),
                         b"only one execution")
        stale = cli_broker.handle(
            ActionRequest("tool-a", Operation.READ_FILE, granted))
        self.assertEqual(stale.status, OutcomeStatus.DENIED_POLICY)
        self.assertEqual(stale.reason.value, "capability_exhausted")
        records = list(read_records(audit_path))
        self.assertEqual(sum(r["result"] == "executed" for r in records), 1)


class TestAuditHardening(BrokerCase):
    def _append(self, log, result="executed", capability_id="cap-1"):
        return log.append(
            request_id="req", tool="tool-a", operation="read_file",
            resource="/tmp/x", params_digest=None, decision="allow",
            reason="ok", confirmation=None, result=result,
            capability_id=capability_id)

    def test_two_writers_do_not_fork_chain(self):
        path = os.path.join(self.root, "audit.jsonl")
        first = AuditLog(path)
        second = AuditLog(path)
        self._append(first)
        self._append(second)
        ok, count, bad = verify_chain(path)
        self.assertTrue(ok, bad)
        self.assertEqual(count, 2)
        self.assertEqual([r["seq"] for r in read_records(path)], [1, 2])

    def test_startup_refuses_tampered_log(self):
        path = os.path.join(self.root, "audit.jsonl")
        log = AuditLog(path)
        self._append(log)
        with open(path) as fh:
            record = json.loads(fh.readline())
        record["tool"] = "rewritten-by-attacker"
        with open(path, "w") as fh:
            fh.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
        with self.assertRaises(AuditIntegrityError):
            AuditLog(path)

    def test_startup_refuses_unreadable_log(self):
        path = os.path.join(self.root, "audit.jsonl")
        with open(path, "w") as fh:
            fh.write("{not json}\n")
        with self.assertRaises(AuditIntegrityError):
            AuditLog(path)

    def test_startup_refuses_symlinked_log(self):
        target = os.path.join(self.root, "real-audit.jsonl")
        with open(target, "w") as fh:
            fh.write("")
        link = os.path.join(self.root, "audit.jsonl")
        os.symlink(target, link)
        with self.assertRaises(AuditIntegrityError):
            AuditLog(link)

    def test_executed_counts_refuse_hash_invalid_record(self):
        path = os.path.join(self.root, "audit.jsonl")
        log = AuditLog(path)
        self._append(log)
        with open(path) as fh:
            record = json.loads(fh.readline())
        record["result"] = "denied_policy"
        with open(path, "w") as fh:
            fh.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
        with self.assertRaises(AuditIntegrityError):
            log.executed_counts_by_capability()

    def test_executed_counts_refuse_hash_valid_but_malformed_execution(self):
        path = os.path.join(self.root, "audit.jsonl")
        log = AuditLog(path)
        self._append(log)
        with open(path) as fh:
            record = json.loads(fh.readline())
        record["capability_id"] = None
        body = {k: v for k, v in record.items() if k != "hash"}
        blob = json.dumps(body, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False).encode("utf-8")
        record["hash"] = hashlib.sha256(
            record["prev_hash"].encode("ascii") + blob).hexdigest()
        with open(path, "w") as fh:
            fh.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
        with self.assertRaises(AuditIntegrityError):
            log.executed_counts_by_capability()

    def test_executed_counts_refuse_malformed_record(self):
        path = os.path.join(self.root, "audit.jsonl")
        log = AuditLog(path)
        self._append(log)
        with open(path, "a") as fh:
            fh.write("{not json}\n")
        with self.assertRaises(AuditIntegrityError):
            log.executed_counts_by_capability()

    def test_executed_counts_refuse_replaced_log_path(self):
        path = os.path.join(self.root, "audit.jsonl")
        log = AuditLog(path)
        self._append(log)
        replacement = os.path.join(self.root, "replacement.jsonl")
        with open(replacement, "w") as fh:
            fh.write("")
        os.replace(replacement, path)
        with self.assertRaises(AuditIntegrityError):
            log.executed_counts_by_capability()

    def test_executed_counts_refuse_deleted_log(self):
        path = os.path.join(self.root, "audit.jsonl")
        log = AuditLog(path)
        self._append(log)
        os.unlink(path)
        with self.assertRaises(AuditIntegrityError):
            log.executed_counts_by_capability()

    def test_executed_counts_refuse_in_place_tail_truncation(self):
        path = os.path.join(self.root, "audit.jsonl")
        log = AuditLog(path)
        self._append(log)
        self._append(log)
        with open(path, "w") as fh:
            fh.write("")
        with self.assertRaises(AuditIntegrityError):
            log.executed_counts_by_capability()

    def test_append_refuses_replaced_log_path(self):
        path = os.path.join(self.root, "audit.jsonl")
        log = AuditLog(path)
        self._append(log)
        replacement = os.path.join(self.root, "replacement.jsonl")
        with open(replacement, "w") as fh:
            fh.write("")
        os.replace(replacement, path)
        with self.assertRaises(AuditIntegrityError):
            self._append(log)

    def test_audit_append_failure_after_execution_is_explicit(self):
        broker, paths, audit_path = self.make(ScriptedConfirmation(default=True))
        with patch("xor_broker.audit.os.write", side_effect=OSError("disk full")):
            out = broker.handle(
                ActionRequest("tool-a", Operation.READ_FILE, paths["granted.txt"]))
        self.assertEqual(out.status, OutcomeStatus.FAILED)
        self.assertEqual(out.reason.value, "audit_failure")
        self.assertIsNone(out.data)
        self.assertIn("executed", out.error)
        self.assertIn("audit", out.error)
        self.assertEqual(os.path.getsize(audit_path), 0)

    def test_init_creates_private_manifest(self):
        manifest = os.path.join(self.root, "config", "xor", "manifest.json")
        with patch("os.path.expanduser", return_value=self.root):
            rc = cmd_init(SimpleNamespace(manifest=manifest))
        self.assertEqual(rc, 0)
        self.assertEqual(stat.S_IMODE(os.stat(manifest).st_mode), 0o600)
        self.assertEqual(
            stat.S_IMODE(os.stat(os.path.dirname(manifest)).st_mode), 0o700)

    def test_init_does_not_chmod_an_existing_custom_parent(self):
        parent = os.path.join(self.root, "custom-grants")
        os.makedirs(parent)
        os.chmod(parent, 0o755)
        manifest = os.path.join(parent, "manifest.json")
        env = {"XDG_CONFIG_HOME": os.path.join(self.root, "config")}
        with patch.dict(os.environ, env, clear=False), \
                patch("os.path.expanduser", return_value=self.root):
            rc = cmd_init(SimpleNamespace(manifest=manifest))
        self.assertEqual(rc, 0)
        self.assertEqual(stat.S_IMODE(os.stat(parent).st_mode), 0o755)
        self.assertEqual(stat.S_IMODE(os.stat(manifest).st_mode), 0o600)

    def test_existing_audit_parent_permissions_are_not_changed(self):
        parent = os.path.join(self.root, "shared-state")
        os.makedirs(parent)
        os.chmod(parent, 0o755)
        AuditLog(os.path.join(parent, "audit.jsonl"))
        self.assertEqual(stat.S_IMODE(os.stat(parent).st_mode), 0o755)

    def test_init_refuses_symlinked_manifest_without_chmodding_target(self):
        target = os.path.join(self.root, "real-manifest.json")
        with open(target, "w") as fh:
            json.dump({"capabilities": []}, fh)
        os.chmod(target, 0o644)
        manifest = os.path.join(self.root, "manifest.json")
        os.symlink(target, manifest)

        with patch("os.path.expanduser", return_value=self.root), \
                patch("sys.stderr", new=io.StringIO()):
            rc = cmd_init(SimpleNamespace(manifest=manifest))

        self.assertEqual(rc, 2)
        self.assertTrue(os.path.islink(manifest))
        self.assertEqual(stat.S_IMODE(os.stat(target).st_mode), 0o644)


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
        # A second daemon must not unlink the live socket.
        with self.assertRaises(BrokerError):
            serve(broker, sock)
        resp = client.write_file(paths["out.txt"], b"via socket")
        self.assertEqual(resp["status"], "executed")
        self.assertEqual(stat.S_IMODE(os.stat(sock).st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
