# xor-broker — the XOR OS permission broker

Default-deny authorisation boundary for tool/assistant-issued actions.
Cooperating tools ask the broker to read/write/list specific resources;
the broker allows a request only when an explicit capability in the
manifest grants that tool that operation on that exact resource. Writes
additionally require per-action user confirmation. Every request —
allowed, denied or failed — is recorded in a hash-chained audit log
that contains digests, never file contents. If the audit append itself
fails, the caller gets an explicit `audit_failure` result, never a
false success.

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
  Selected-directory grants are inode-pinned, and the executor checks
  the opened directory against that pin before listing.
- Path traversal and symlink escapes fail closed. Enforcement is
  FD-anchored (`dir_fd` walks with `O_NOFOLLOW`), not string-prefix
  checks, so policy/executor races cannot redirect access. Manifest
  loading uses the same no-follow + regular-file policy as
  `xor-broker init`.
- Unpinned writes stage content inside a private `0700` staging directory
  in the target's parent, anchored to the opened parent FD so the final
  rename stays on the same filesystem. The staged file is created `0600`;
  the destination rwx mode is applied while it is still inside the
  owner-verified directory, so confidentiality comes from the `0700`
  directory rather than the file mode. Another user cannot traverse the
  directory, read the staged bytes or substitute the source entry; a
  pathname swap of
  the staging directory itself cannot redirect the FD-anchored `rename`.
  The executor also re-checks the staged file's inode as defence in depth.
  A failed write, fsync or rename leaves the original content untouched
  and removes the staging files. Ordinary rwx mode bits are copied;
  ownership, ACLs, extended attributes, setuid/setgid bits and hard-link
  identity are not.
- Pinned write targets are opened without `O_TRUNC` and verified against
  the file-selection pin before any change. Pinned writes update that same
  inode in place (renaming would break the pin and hard links): the new
  bytes are staged and the old bytes backed up first, then written from
  offset zero and truncated to the new length only after every new byte is
  accepted. A reported write failure restores the original (a rollback that
  itself fails is reported explicitly and may leave partial content). Replacing or deleting the
  selected file fails closed, is not recreated, and leaves the replacement
  untouched.
- Zero-progress or oversized `write()` results fail explicitly instead of
  looping or reporting success.
- `max_uses` is durable in the audit log and enforced under a
  cross-process use lock, so concurrent daemon/CLI requests cannot
  exceed the limit. Counts are rebuilt by verifying the chain from the
  same locked file descriptor; malformed/hash-invalid records, a
  replaced/deleted log path, and in-process tail truncation fail closed.
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

Execution and audit append are not crash-atomic. A crash after the
action but before the durable append can leave an execution uncounted
against `max_uses`; a tail truncation made before a verifier starts is
not detectable from the log alone. An append failure is reported as
`audit_failure` and does not return any action result data.

Write-specific residuals: unpinned atomic replacement changes the inode,
so hard links to the old file keep the old content and ownership/ACLs/
extended attributes are not copied (ordinary rwx mode bits are; setuid/
setgid bits are cleared). Unpinned writes need write permission on the
containing directory for staging and rename, even if the file itself is
writable. The `0700` staging directory excludes other users, but a
same-UID process can enter it (same-user code is outside the broker
boundary); the staged-inode check narrows, but cannot eliminate, a
last-instant same-UID source swap. Another user with write access to a
non-sticky parent can rename or remove the staging directory entry, which
can cause a reported failure or leave an empty directory behind, but
cannot read the staged bytes or substitute the source because the
directory remains `0700` and rename uses the held FD. A destination
replacement made in the last instant between the final identity check and
`rename` can be overwritten, though the write cannot escape the granted
directory because `rename` does not follow symlinks. Pinned in-place
writes are rollback-protected against *reported* errors, not crash-atomic:
a crash between truncate and restore can still leave partial content.
Rollback needs read permission on the pinned file; without it the write
fails before the target is modified. Concurrent writers to the same inode
are not locked.

## Layout

`models.py` typed API · `policy.py` default-deny engine · `paths.py`
FD-safe filesystem access · `executor.py` the only file-touching code ·
`confirm.py` confirmation providers/tokens · `audit.py` hash-chained log ·
`manifest.py` grant loading/validation · `server.py` Unix-socket daemon +
client · `cli.py` `xor-broker` command.
