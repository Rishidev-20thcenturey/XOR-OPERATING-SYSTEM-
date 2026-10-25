# XOR OS architecture (baseline)

```
                    ┌─────────────────────────────────────────────┐
  future (M3)       │  Assistant runner (DISABLED stub today)     │
  ─ - ─ - ─ - ─     │  declared tools -> broker ops only          │
                    └──────────────────┬──────────────────────────┘
 cooperating tools  demo-reader/writer │ BrokerClient (Unix socket)
                    ───────────────────┤
                    ┌──────────────────▼──────────────────────────┐
                    │  xor-broker daemon (per-user, NOT root)     │
                    │                                             │
                    │  PolicyEngine ── default-deny, manifest     │
                    │  Confirmation ── GTK/CLI, digest-bound, 1×  │
                    │  Executor ────── ONLY code touching files   │
                    │  AuditLog ────── JSONL, SHA-256 chained     │
                    └──────────────────┬──────────────────────────┘
                                       │ FD-anchored opens (O_NOFOLLOW)
                    ┌──────────────────▼──────────────────────────┐
  granted files     │  User files (only explicitly granted scope) │
                    └─────────────────────────────────────────────┘

  Desktop (XFCE) · Activity viewer reads audit log · Debian 13 + Linux kernel
```

## Components and boundaries

| Component | Path | Responsibility | Depends on |
|---|---|---|---|
| Broker core | `broker/src/xor_broker/` | Authorisation, confirmation, execution, audit | Python stdlib only |
| Broker daemon/CLI | `.../server.py`, `.../cli.py` | Unix-socket IPC (`0600`), `xor-broker demo/init/serve/request/audit/grants` | broker core |
| GTK confirmation | `.../confirm_gtk.py` | Write-approval dialog (lazy `gi` import; fail-closed if absent) | PyGObject at runtime only |
| Activity viewer | `desktop/xor_activity.py`, `activity_core.py` | Inspect audit log; GTK + `--text` modes | audit log format |
| XOR Shell “Aurora” | `desktop/xor_shell/` | Glass panel, floating dock, launcher, Command Centre, Control Centre; testable non-GTK core | GTK 3, optional Wnck, XFCE/X11 session |
| Theme/launchers | `desktop/theme/`, `desktop/applications/` | XFCE defaults, Royal Aurora wallpapers/logo, menu entries, broker + shell autostart | XFCE |
| Assistant stub | `assistant/src/xor_assistant/` | Future connection point; **disabled**, read-only declared surface | broker client (future) |
| Security corpus | `security/` | 200 deterministic scenarios + acceptance criteria | broker public API |
| OS image | `os/live-build/` | Debian trixie + XFCE live ISO assembling all of the above | Debian Live Build |
| Tooling | `tools/` | build-iso, run-qemu (fixed virt target), test, validate | — |

Separation rule: the **policy engine has no UI, storage or model
dependencies**. Replacing the confirmation dialog, the log viewer or
the desktop shell must not require touching `policy.py`, `paths.py` or
`executor.py`.

## Request lifecycle

1. Tool sends `ActionRequest{tool, operation, path, content?, description}`.
2. `PolicyEngine.evaluate` — denied unless a capability matches tool +
   operation + scoped resource + right + not expired/exhausted.
   `untrusted_context` (document/tool-output text) is never read.
3. Writes only: `ConfirmationManager` asks the user; approval mints a
   **single-use token bound to the action digest** (tool+op+canonical
   path+content SHA-256), valid 120 s. Deny is the default on every path.
4. `Executor` re-verifies the token and opens the target FD-anchored
   inside the grant (`dir_fd` walk, `O_NOFOLLOW` on every component),
   so symlink/traversal/race tricks fail closed.
5. Exactly one audit record per request — allowed, denied or failed —
   appended from a `finally` block. Records hold digests, never content.

## Key decisions (ADRs in `docs/adr/`)

1. Reuse Linux/Debian; no custom kernel, package manager or browser.
2. Broker in Python stdlib-only for auditability and zero-cost tooling.
3. FD-anchored path enforcement instead of string-prefix checks.
4. Hash-chained JSONL audit with explicit tamper-evidence limits.

## What this is not (yet)

* Not system-wide enforcement: only requests *through the broker API*
  are mediated. Same-user processes can still open your files directly.
  Isolating tool/assistant processes (bubblewrap/Flatpak portals) so
  the broker is their only path is the Milestone-4 direction.
* Tool identity over the socket is self-declared (peer UID/PID are
  recorded, names are not authenticated yet).
* No updater yet — `docs/UPDATE_DESIGN.md` is design only.
* No LLM, no network capability for tools, by design.
