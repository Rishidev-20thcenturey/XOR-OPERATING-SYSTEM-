# Threat model — XOR OS baseline

Honesty rule for this document: every protection below is labelled
**Prevents**, **Detects** or **Does not address**, and only claims what
is implemented *and tested* (see `docs/TEST_RESULTS.md`).

## Assets and actors

Assets: user files, user consent (write approvals), audit integrity,
the user's trust that "denied" means did-not-happen.
Actors: misleading documents/tool output (prompt injection), buggy or
malicious cooperating tools, malicious local same-user processes,
network attackers, a compromised update channel (future), root/kernel
compromise (out of scope, stated plainly).

## Trust boundary

The broker mediates calls made **through its API** by cooperating
tools. It runs as the user, never root. It is *not* a syscall firewall
and does not by itself confine arbitrary processes.

## Threats

| # | Threat | Baseline position |
|---|---|---|
| T1 | Document text instructs the assistant/tool to read/exfiltrate files (prompt injection) | **Prevents** (within broker): policy never reads untrusted text; 50 corpus scenarios + twin checks prove decisions are text-independent. No network op exists to grant. |
| T2 | Tool requests outside its grant (sibling files, parent dir, other tool's grant) | **Prevents**: default-deny + exact scope matching; 100 out-of-grant corpus scenarios, 0 executed. |
| T3 | Path traversal (`../`, odd spellings) | **Prevents**: canonicalisation at policy + FD-anchored enforcement rejecting `..`/escapes. |
| T4 | Symlink escape (inside granted dir → outside) | **Prevents**: `O_NOFOLLOW` on every component; symlink reads/writes fail closed (corpus + unit tests, incl. no side-effects on denied write-through). |
| T5 | TOCTOU: swap path between check and open | **Prevents (mostly)**: traversal anchored to open directory FDs. Unpinned writes stage content in a private `0700` directory created in the target's parent and anchored to the opened parent FD; the staged file is created `0600` (destination mode is applied inside the private directory), its inode is re-checked, and the final `rename` uses the held staging FD, so another user cannot read or substitute the staged bytes even if the staging directory's *name* is swapped. The destination entry is also re-checked as a regular non-symlink file before rename, so a failed write leaves the old content intact. For pinned file-selection write grants, the target is opened without `O_TRUNC`, the opened inode is checked against the grant pin, and only then is that same inode updated in place with a staged backup/rollback copy; replacing or deleting the selected file fails closed and leaves the replacement untouched. A selected-directory `list_dir` grant is also inode-pinned, and the executor checks the opened directory's `(st_dev, st_ino)` before listing. Staging-directory cleanup captures the created directory's identity immediately after `mkdir`; setup-failure cleanup removes the name only while it still names that exact directory, and in a shared non-sticky parent (or a sticky parent owned by an untrusted user) it prefers leaving an empty orphan over risking deletion of a substituted entry. Residuals: hard-linked inode inside a granted dir aliasing an outside inode, inode reuse after deletion, a last-instant destination replacement between the final unpinned identity check and `rename`, a last-instant source swap by a *same-UID* process, non-crash-atomic pinned rollback, parent-directory-write DoS/leftover staging names, and the microscopic cleanup-check-to-`rmdir` window if parent permissions change concurrently. Documented in `paths.py` and `broker/README.md`. |
| T6 | Write slips through without the user noticing | **Prevents**: every write needs a fresh confirmation; deny default (EOF/Esc/timeout/provider crash all deny); approval bound to exact action digest, single-use, 120 s TTL. Replayed/stale/cross-action tokens rejected in tests. |
| T7 | Fake "user approved" text in a document | **Prevents**: approvals come only from the confirmation provider; corpus category `injection_fake_approval` verifies denial. |
| T8 | Malformed/over-broad capability manifest | **Prevents**: strict validation, whole load fails closed; unsupported ops (delete/network/exec) are ungrantable. Manifest loading (not just `init`) refuses symlinks and non-regular files. |
| T9 | Audit log leaks private content | **Prevents by design + test**: records contain digests/metadata only; corpus scans logs for content markers (0 leaks). Audit file mode 0600; directories created by XOR are 0700. |
| T10 | Audit log tampering after the fact | **Detects**: SHA-256 hash chain; `verify` finds edits/deletions/reordering (tested), and the broker refuses to start on a broken/unreadable chain. `max_uses` counts are rebuilt by verifying the chain from the same opened FD under a shared lock; malformed or hash-invalid records are rejected, and a replaced/deleted path or in-process tail truncation fails closed. If an append cannot be written, the broker returns an explicit `audit_failure` (no success, no result data). **Limits:** an attacker controlling both log and verifier (e.g. root) can forge a consistent chain; truncating the tail before a process starts, or deleting the whole file before startup, is not detectable from the log alone. External head anchoring remains future work. A local chain is tamper-*evidence*, not tamper-proofing. |
| T11 | Tool impersonation over the Unix socket | **Partially addresses (cooperating tools)**: with `--token-dir`, each tool has a random bearer token in a private `0700` directory (`0600` files); the broker compares in constant time, rejects missing/wrong/non-string tokens, and rechecks after confirmation so revocation during the prompt blocks the write. The sandbox launcher binds only that tool's token file. **Limits:** tokens are not bound to peer PID/UID, a bubblewrap instance, or a hardware key; a same-UID process that can read the token directory or steal a token can still impersonate that tool. Peer credentials are reported, not used for authorization. |
| T12 | Malicious same-user process bypasses broker entirely (opens files itself) | **Partially mitigates for opted-in tools**: `xor-broker launch` runs a cooperating tool under bubblewrap with `--unshare-all`, a cleared environment, tmpfs home/tmp, read-only system directories, and only the broker socket plus its own token file. It is not the default for every tool or the future assistant runner, and processes outside the sandbox still bypass the broker. No syscall firewall is claimed. |
| T13 | Compromised user account / root / kernel / firmware | **Does not address**: out of scope for a user-space broker. Disk encryption, secure boot etc. belong to later system hardening. |
| T14 | Malicious/botched OS update | **Does not address yet**: no updater exists. A/B design + failure matrix in `UPDATE_DESIGN.md`; nothing is signed or rolled back today, and we do not claim it is. |
| T15 | Denial of service via request flooding / huge files | Partial: reads capped at 16 MiB, accepted requests at 32 MiB, and per-connection socket timeouts stop one silent client holding the loop forever. The daemon is still single-threaded and has no rate limiting, so flooding remains a local DoS. `max_uses` is durable and enforced under a cross-process advisory lock held across the policy check, execution and audit append, so concurrent daemon/CLI requests cannot both consume the last use. This is concurrency-atomic, not crash-atomic: a crash after acting but before the audit append can undercount uses. Likewise, if the append fails, the caller receives `audit_failure` and no result data, but the already-performed action is not retroactively undone. A pending confirmation holds that lock and can delay other broker clients; same-user code that bypasses the broker is still outside this boundary (T12). |
| T16 | User confused into approving a harmful write | Mitigated, not solved: prompt shows tool, operation, exact target, description and bounded content preview + digest; deny is the focused default. UX phishing research is future work. |

## Assumptions

* The Linux kernel, Debian userspace and the user's session are not
  already compromised.
* The capability manifest itself is user-controlled configuration.
  XOR-created manifest/audit files are 0600 and directories it creates
  are 0700; user-chosen custom paths and their existing parent
  directories are the user's responsibility to review. `xor-broker
  grants` displays exactly what is granted.
* Timestamps use the local clock; expiry is a usability control, not a
  monotonic security guarantee (see UPDATE_DESIGN re: downgrades).

## Explicit non-claims

Not formally verified. Not enterprise-secure. Not unbreakable. Not
system-enforced. It is a small, tested authorisation boundary for
cooperating tools — the foundation stronger isolation will build on.
