# xor-broker — the XOR OS permission broker

Default-deny authorisation boundary for tool/assistant-issued actions.
Cooperating tools ask the broker to read/write/list specific resources;
the broker allows a request only when an explicit capability in the
manifest grants that tool that operation on that exact resource. Writes
additionally require per-action user confirmation. Every request —
allowed, denied or failed — is recorded in a hash-chained audit log
that contains digests, never file contents.

## Quick start

```sh
cd broker
python3 -m pip install -e .        # or just: PYTHONPATH=src python3 -m xor_broker.cli
xor-broker demo                    # self-contained demo in a temp sandbox
python3 -m pytest                  # unit tests (also runs under unittest)
```

## Concepts

| Term | Meaning |
|---|---|
| Capability | One grant: `{tool, operation, resource(file|dir,path), rights, expires_at?, max_uses?}` |
| Manifest | JSON file of capabilities; strictly validated, fails closed |
| Confirmation | Per-write user approval bound to the exact action digest; single use, 120 s TTL, deny default |
| Audit log | `~/.local/state/xor/broker/audit.jsonl`, mode 0600, SHA-256 hash-chained |

Operations: `read_file`, `write_file`, `list_dir` are enforceable.
`delete_file`, `network_send`, `exec_process` exist in the API so they
can be **denied explicitly and audited**; manifests cannot grant them.

## Safety properties (implemented, tested)

- Selecting/granting one file never grants its parent directory.
  Directory listing requires an explicit `dir`-scoped grant; a
  `list_dir` grant with `file` scope is rejected at manifest load.
- Path traversal and symlink escapes fail closed. Enforcement is
  FD-anchored (`dir_fd` walks with `O_NOFOLLOW`), not string-prefix
  checks, so policy/executor races cannot redirect access.
- `max_uses` is durable in the audit log and enforced under a
  cross-process use lock, so concurrent daemon/CLI requests cannot
  exceed the limit.
- An approval for action A cannot authorise action B (digest binding),
  cannot be replayed (single use), and expires.
- Untrusted document text (`untrusted_context`) is never read by the
  policy engine — see the 200-scenario corpus in `../security/`.

## Honest limits

The broker mediates requests made **through its API**. It does not
intercept syscalls of arbitrary same-user processes, and tool identity
over the Unix socket is self-declared in the baseline. Process
isolation (bubblewrap/Flatpak portals) for the future assistant runner
is Milestone 4. See `../docs/THREAT_MODEL.md`.

## Layout

`models.py` typed API · `policy.py` default-deny engine · `paths.py`
FD-safe filesystem access · `executor.py` the only file-touching code ·
`confirm.py` confirmation providers/tokens · `audit.py` hash-chained log ·
`manifest.py` grant loading/validation · `server.py` Unix-socket daemon +
client · `cli.py` `xor-broker` command.
