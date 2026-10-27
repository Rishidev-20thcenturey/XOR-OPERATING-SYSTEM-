# Test results — actual, from available sandboxes (2026-10-25; hardening passes 2026-10-27)

Original baseline environment: Debian 12 sandbox, Python 3.11.2,
2 vCPU / 3.8 GiB, **no QEMU, no /dev/kvm, no live-build, no Debian
mirror access, no GTK/PyGObject** (egress limited to github/pypi/npm).
The 2026-10-27 hardening pass was re-run on Termux/Android with Python
3.13.13 under the same unprivileged, headless, offline constraints,
because the Debian sandbox was unavailable. The final follow-up run in
this document is from **2026-10-27 19:31 +0530** on that environment.
Everything verifiable in each environment was run; what could not run is
listed as NOT RUN with the exact blocker. Reproduce locally with
`tools/test.sh`.

## Unit tests — PASS (75 tests)

Latest `tools/test.sh` run (unittest fallback) → **75 passed**:
61 broker tests (including corpus-as-test), 3 assistant-gate tests and
11 desktop tests. Exact durations from the final run: 61 tests in
2.611 s, 3 tests in 0.000 s, 11 tests in 0.040 s. Covers: default-deny
matrix, exact file/dir scoping, traversal & symlink fail-closed (incl.
write-through leaving target untouched), selected-directory grants
issuing `dir` scope and actually listing, replacement of a selected
directory rejected by its inode pin, rejection of file-scoped `list_dir`
grants, confirmation digest-binding / expiry / replay rejection,
deny-default CLI (blank, EOF, "yes" ≠ approve), provider-crash ⇒ deny,
audit completeness + 0600 + tamper detection, FD-anchored chain
verification and `max_uses` counts that reject malformed/hash-invalid
records, replaced/deleted/in-place-truncated logs, explicit
`audit_failure` when an append fails after an action executed, durable
`max_uses` across a broker rebuild, concurrent thread/process requests
and a daemon/CLI sharing one audit log all respecting a single-use
limit, two-writer audit locking, fail-closed startup on a
tampered/unreadable/symlinked chain, private manifest creation, shared
no-follow/regular-file manifest loading (symlink and non-regular
rejection) plus refusal to chmod a symlinked manifest target, 0600 mode
on newly created write targets, daemon singleton locking, manifest
strictness (unsupported ops ungrantable), real Unix-socket round-trip,
assistant disabled + read-only surface, activity filters/search/chain
(and malformed-log fallback), and the Aurora shell core (desktop-entry
parsing/search/grouping, launch argv field-code stripping, config
sanitising/round-trip and malformed-config fallback, battery/network/
GiB memory parsing, wallpaper catalog, panel-opacity CSS generation,
Control Centre text status).

Failures found during development were fixed in **product code**:
symlinked-dir `ENOTDIR` maps to fail-closed `PathEscapeError`, and the
shell's KiB→GiB memory conversion was corrected after live output
exposed it. One flawed tamper-test setup was also corrected. No
security expectation was weakened.

The 2026-10-27 hardening pass fixed these audited defects: QEMU
identifiers (`/dev/kvm`, `accel=kvm`, `virtio-vga`, `usb-kbd`); the install hook
mutating demo-tool shebangs (now PYTHONPATH wrappers, preserving the
docstring); saved-but-unapplied dock position, panel opacity and reduce-motion
settings; world-readable starter manifest permissions; in-memory-only
`max_uses`; audit-log writer races and startup verification; daemon
socket unlinking/singleton handling; and malformed config/audit-log
crashes in the shell and Activity viewer. Network/battery sysfs
permission failures found on Termux were also converted to safe
fallbacks.

The follow-up hardening pass fixed: audit chain reads/counts now use and
verify the same locked FD (malformed/hash-invalid records and
replaced/deleted/in-process-truncated logs fail closed); append failures
after execution return an explicit `audit_failure` with no result data;
`list_dir` verifies the opened directory's inode pin; manifest loading
shares `init`'s no-follow/regular-file policy; and the unittest harness
no longer pipes test output through `tail` in a way that could mask a
failure.

## Security corpus — PASS (all 10 criteria)

`python3 security/run_corpus.py` (200 scenarios + 50 injection twins;
final run `OVERALL: PASS`):

| # | Criterion | Result |
|---|---|---|
| 1 | Zero out-of-grant actions execute | PASS (0) |
| 2 | Zero unauthorised network sends | PASS (0) |
| 3 | Every executed write confirmed first | PASS (0 unconfirmed) |
| 4 | Every request audited, chain valid | PASS (0 failures) |
| 5 | No raw content in audit logs | PASS (0 leaks) |
| 6 | ≥95% legitimate approved actions succeed | PASS (45/45 = 100%) |
| 7 | Unsupported ops identified, never faked | PASS (32 req, 0 mislabelled) |
| 8 | Untrusted text never changes a decision (twins) | PASS (0 changed) |
| 9 | Denied writes side-effect-free | PASS (0) |
| 10 | All scenario expectations met | PASS (0 mismatches) |

## Static validation — PASS

`tools/validate.py`: 34 Python files compile, example manifest valid,
corpus regenerates byte-identically, 3 XFCE XML files well-formed,
7 `.desktop` entries and Aurora commands sane, live-build tree and
Aurora install wiring complete, QEMU device identifiers and install-hook
demo wrappers correct, 10 shell scripts parse, legacy PNG 1920×1080,
CSS classes/braces, SVG logo, Royal Aurora JPEG and XFCE default agree,
and headless XOR Shell/Control Centre commands run. The corpus's
unsupported-operation payload marker was made path-independent so the
content-redaction check cannot false-positive on a sandbox path
containing the old generic string `data`. In the final Termux run,
pyflakes and ShellCheck were **not installed**, so `tools/validate.py`
reported both as `SKIPPED`; no clean-lint claim is made from this run.
Earlier Debian/pytest-venv runs and GitHub Actions PR #2 (Python 3.11,
Python 3.12 and ShellCheck jobs) were green historically, but were not
re-run here.

## Demo executions — PASS

* `xor-broker demo`: read granted ✓ executed · sibling secret ✗
  denied_policy · traversal ✗ denied_policy · confirmed write ✓ ·
  network ✗ denied_unsupported; 5 audit records, chain valid.
* `xor-broker init` + `grants` in a throwaway HOME: demo grants created exactly as documented.
* Aurora headless CLI: `--status-json` reports shell 0.2.0, Royal Aurora
  wallpaper, live network/battery and **0.2 / 3.8 GiB** memory;
  `--list-apps` discovers first-party Control Centre/Shell entries;
  Control Centre `--text` prints appearance, host and broker summary.
  The Royal Aurora wallpaper and SVG logo were visually inspected.

## NOT RUN — blocked by sandbox, commands ready

| Item | Blocker (verbatim) | Run instead |
|---|---|---|
| ISO build | `ERROR: Debian Live Build ('lb') not found.` (no lb, no mirror, unprivileged) | On a Debian/Ubuntu machine: `sudo apt install live-build && sudo tools/build-iso.sh` |
| QEMU boot | `ERROR: qemu-system-x86_64 not found.` (also no /dev/kvm) | `sudo apt install qemu-system-x86 ovmf && tools/run-qemu.sh` |
| GTK UI behaviour | no display / no PyGObject | After first ISO boot: verify Aurora panel/dock/launcher/Command Centre/Control Centre, Wnck window buttons and workspaces; run `xor-demo-writer`, verify Deny-focused dialog, Esc denies, 120 s timeout; open XOR Activity, check Ctrl+1–4 / Ctrl+F / Esc. Code is compile-checked; non-GTK cores and text/status modes are tested. |

Consequence, stated plainly: **Milestone 0's "bootable image" is
configured and scripted but not yet boot-verified, and Aurora is a
tested-core preview rather than a daily-used shell.** Milestone 1
(broker) acceptance is fully demonstrated. First team-machine task:
build, boot, exercise Aurora + GTK confirmation, and replace this
section's NOT RUN rows with evidence.
