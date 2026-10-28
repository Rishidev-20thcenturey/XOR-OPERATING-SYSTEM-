"""Regression tests for tool tokens, sandbox argv, and grant editing."""
import json
import os
import socket
import stat
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from xor_broker.audit import AuditLog, read_records, verify_chain  # noqa: E402
from xor_broker import cli  # noqa: E402
from xor_broker.broker import Broker  # noqa: E402
from xor_broker.confirm import ConfirmationManager, ScriptedConfirmation  # noqa: E402
from xor_broker.manifest import (  # noqa: E402
    add_capability, grant_for_selected_file, load_manifest, parse_manifest,
    remove_capability,
)
from xor_broker.models import (  # noqa: E402
    ActionRequest, Operation, OutcomeStatus, ReasonCode,
)
from xor_broker.policy import PolicyEngine  # noqa: E402
from xor_broker.sandbox import SandboxConfig, build_bubblewrap_args  # noqa: E402
from xor_broker.server import BrokerClient, BrokerError, serve  # noqa: E402
from xor_broker.tokens import TokenStore, TokenStoreError  # noqa: E402


class TokenStoreCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.store = TokenStore(os.path.join(self.root, "tokens"))
        self.granted = os.path.join(self.root, "granted.txt")
        self.out = os.path.join(self.root, "out.txt")
        with open(self.granted, "w") as fh:
            fh.write("read me")
        with open(self.out, "w") as fh:
            fh.write("old")

    def tearDown(self):
        self.tmp.cleanup()

    def broker(self, provider=None):
        manifest = {"capabilities": [
            {"capability_id": "r", "tool": "tool-a", "operation": "read_file",
             "resource": {"type": "file", "path": self.granted}, "rights": ["read"]},
            {"capability_id": "w", "tool": "tool-a", "operation": "write_file",
             "resource": {"type": "file", "path": self.out}, "rights": ["write"]},
        ]}
        return Broker(
            PolicyEngine(parse_manifest(manifest)),
            ConfirmationManager(provider or ScriptedConfirmation(default=True)),
            AuditLog(os.path.join(self.root, "audit.jsonl")),
            token_store=self.store,
        )

    def test_store_is_private_and_tokens_rotate(self):
        first = self.store.issue("tool-a")
        self.assertEqual(self.store.get("tool-a"), first)
        self.assertEqual(stat.S_IMODE(os.stat(self.store.directory).st_mode), 0o700)
        token_path = self.store.path_for("tool-a")
        self.assertEqual(stat.S_IMODE(os.stat(token_path).st_mode), 0o600)
        second = self.store.issue("tool-a")
        self.assertNotEqual(first, second)
        self.store.revoke("tool-a")
        self.assertIsNone(self.store.get("tool-a"))

    def test_tool_names_cannot_escape_the_store(self):
        for bad in ("../x", "a/b", "", ".hidden"):
            with self.assertRaises(TokenStoreError):
                self.store.issue(bad)

    def test_broker_requires_matching_token_for_every_tool(self):
        token = self.store.issue("tool-a")
        broker = self.broker()
        ok = broker.handle(ActionRequest(
            "tool-a", Operation.READ_FILE, self.granted, tool_token=token))
        self.assertEqual(ok.status, OutcomeStatus.EXECUTED)

        for supplied in (None, "wrong", ["not-a-string"]):
            denied = broker.handle(ActionRequest(
                "tool-a", Operation.READ_FILE, self.granted, tool_token=supplied))
            self.assertEqual(denied.status, OutcomeStatus.DENIED_POLICY)
            self.assertEqual(denied.reason, ReasonCode.UNAUTHENTICATED_TOOL)

        unknown = broker.handle(ActionRequest(
            "tool-b", Operation.READ_FILE, self.granted, tool_token=token))
        self.assertEqual(unknown.status, OutcomeStatus.DENIED_POLICY)
        self.assertEqual(unknown.reason, ReasonCode.UNAUTHENTICATED_TOOL)

    def test_revocation_while_confirmation_is_pending_blocks_write(self):
        token = self.store.issue("tool-a")

        def revoke_and_approve(_prompt):
            self.store.revoke("tool-a")
            return True

        broker = self.broker(ScriptedConfirmation(answers=revoke_and_approve))
        outcome = broker.handle(ActionRequest(
            "tool-a", Operation.WRITE_FILE, self.out, content=b"new",
            description="test write", tool_token=token))
        self.assertEqual(outcome.status, OutcomeStatus.DENIED_POLICY)
        self.assertEqual(outcome.reason, ReasonCode.UNAUTHENTICATED_TOOL)
        with open(self.out) as fh:
            self.assertEqual(fh.read(), "old")

    def test_malformed_wire_request_is_audited(self):
        token = self.store.issue("tool-a")
        broker = self.broker()
        sock_path = os.path.join(self.root, "broker.sock")
        thread = threading.Thread(target=serve, args=(broker, sock_path), daemon=True)
        thread.start()
        for _ in range(100):
            if os.path.exists(sock_path):
                break
            threading.Event().wait(0.05)

        client = BrokerClient("tool-a", sock_path, tool_token=token)
        self.assertEqual(client.read_file(self.granted), b"read me")

        # The default client path reads the tool's token file, not an argv
        # secret.  A wrong file token must fail closed.
        old_dir = os.environ.get("XOR_TOKEN_DIR")
        os.environ["XOR_TOKEN_DIR"] = self.store.directory
        try:
            self.assertEqual(
                BrokerClient("tool-a", sock_path).read_file(self.granted),
                b"read me")
            with self.assertRaises(BrokerError):
                BrokerClient("tool-a", sock_path,
                             tool_token="wrong").read_file(self.granted)
            self.store.revoke("tool-a")
            with self.assertRaises(BrokerError):
                BrokerClient("tool-a", sock_path).read_file(self.granted)
        finally:
            if old_dir is None:
                os.environ.pop("XOR_TOKEN_DIR", None)
            else:
                os.environ["XOR_TOKEN_DIR"] = old_dir
            self.store.issue("tool-a", token)

        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as raw:
            raw.connect(sock_path)
            raw.sendall(b'{"tool": "tool-a"}\n')
            self.assertIn(b"malformed", raw.recv(4096))

        records = list(read_records(os.path.join(self.root, "audit.jsonl")))
        self.assertTrue(any(r["tool"] == "(malformed)" for r in records))
        ok, _n, bad = verify_chain(os.path.join(self.root, "audit.jsonl"))
        self.assertTrue(ok, bad)

    def test_token_store_refuses_a_public_directory(self):
        public = os.path.join(self.root, "public-tokens")
        os.mkdir(public, 0o700)
        os.chmod(public, 0o755)
        with self.assertRaises(TokenStoreError):
            TokenStore(public)

    def test_serve_refuses_a_public_socket_directory(self):
        public = os.path.join(self.root, "public")
        os.mkdir(public, 0o700)
        os.chmod(public, 0o755)
        with self.assertRaises(BrokerError):
            serve(self.broker(), os.path.join(public, "broker.sock"))


class SandboxArgsCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.token = os.path.join(self.root, "token")
        with open(self.token, "w") as fh:
            fh.write("secret-token-value")
        os.chmod(self.token, 0o600)
        self.sock_path = os.path.join(self.root, "broker.sock")
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(self.sock_path)
        self.sock.listen(1)

    def tearDown(self):
        self.sock.close()
        self.tmp.cleanup()

    def args(self, **kwargs):
        config = SandboxConfig(
            tool="tool-a", token_file=self.token, socket_path=self.sock_path,
            command=["/usr/bin/true"], **kwargs)
        return build_bubblewrap_args(config)

    def test_token_is_not_in_argv_and_only_socket_file_is_bound(self):
        args = self.args()
        joined = "\x00".join(args)
        self.assertNotIn("secret-token-value", joined)
        self.assertIn("--clearenv", args)
        self.assertIn("--unshare-net", args)
        self.assertIn("--die-with-parent", args)
        self.assertIn("--ro-bind", args)
        self.assertIn("--bind", args)
        self.assertEqual(
            args[args.index("--bind") + 1:args.index("--bind") + 3],
            [self.sock_path, "/run/xor-broker.sock"])
        self.assertEqual(
            args[args.index("--ro-bind", args.index("--bind")) + 1:
                 args.index("--ro-bind", args.index("--bind")) + 3],
            [self.token, "/run/xor-token"])

    def test_network_can_be_shared_explicitly(self):
        args = self.args(network_isolated=False)
        self.assertIn("--share-net", args)
        self.assertNotIn("--unshare-net", args)

    def test_unsafe_paths_and_commands_are_rejected(self):
        with self.assertRaises(ValueError):
            build_bubblewrap_args(SandboxConfig(
                "tool-a", self.token, self.sock_path, []))
        with self.assertRaises(ValueError):
            build_bubblewrap_args(SandboxConfig(
                "tool-a", self.token, self.sock_path, ["/bin/sh", "a\x00b"]))
        with self.assertRaises(ValueError):
            build_bubblewrap_args(SandboxConfig(
                "tool-a", os.path.join(self.root, "missing"),
                self.sock_path, ["/usr/bin/true"]))
        link = os.path.join(self.root, "token-link")
        os.symlink(self.token, link)
        with self.assertRaises(ValueError):
            build_bubblewrap_args(SandboxConfig(
                "tool-a", link, self.sock_path, ["/usr/bin/true"]))
        with self.assertRaises(ValueError):
            build_bubblewrap_args(SandboxConfig(
                "tool-a", self.token, self.sock_path, ["/usr/bin/true"],
                read_only_binds=["/"]))


class ManifestEditingCase(unittest.TestCase):
    def test_grant_and_revoke_round_trip(self):
        with tempfile.TemporaryDirectory() as root:
            selected = os.path.join(root, "selected.txt")
            with open(selected, "w") as fh:
                fh.write("selected")
            manifest = os.path.join(root, "manifest.json")
            with open(manifest, "w") as fh:
                json.dump({"capabilities": []}, fh)
            os.chmod(manifest, 0o600)

            cap = grant_for_selected_file(
                "tool-a", Operation.READ_FILE, selected, "selected-read")
            add_capability(manifest, cap)
            loaded = load_manifest(manifest)
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].capability_id, "selected-read")
            self.assertIsNotNone(loaded[0].pin_dev)
            self.assertIsNotNone(loaded[0].pin_ino)

            remove_capability(manifest, "selected-read")
            self.assertEqual(load_manifest(manifest), [])
            self.assertEqual(stat.S_IMODE(os.stat(manifest).st_mode), 0o600)

    def test_cli_grant_and_revoke_wire_the_manifest(self):
        with tempfile.TemporaryDirectory() as root:
            selected = os.path.join(root, "selected.txt")
            with open(selected, "w") as fh:
                fh.write("selected")
            manifest = os.path.join(root, "manifest.json")
            with open(manifest, "w") as fh:
                json.dump({"capabilities": []}, fh)
            self.assertEqual(cli.main([
                "--manifest", manifest, "grant", "--tool", "tool-a",
                "--operation", "read_file", "--path", selected,
                "--capability-id", "cli-read"]), 0)
            self.assertEqual(load_manifest(manifest)[0].capability_id, "cli-read")
            self.assertEqual(cli.main([
                "--manifest", manifest, "revoke", "cli-read"]), 0)
            self.assertEqual(load_manifest(manifest), [])
