# Test results — actual, from available sandboxes (2026-10-25; hardening pass 2026-10-27)

Original baseline environment: Debian 12 sandbox, Python 3.11.2,
2 vCPU / 3.8 GiB, **no QEMU, no /dev/kvm, no live-build, no Debian
mirror access, no GTK/PyGObject** (egress limited to github/pypi/npm).
The 2026-10-27 hardening pass was re-run on Termux/Android with Python
3.13.13 under the same unprivileged, headless, offline constraints,
because the Debian sandbox was unavailable. Everything verifiable in
each environment was run; what could not run is listed as NOT RUN with
the exact blocker. Reproduce locally with `tools/test.sh`.

## Unit tests — PASS (62 tests)

Latest `tools/test.sh` run (unittest fallback) → **62 passed**:
48 broker tests (including corpus-as-test), 3 assistant-gate tests and
11 desktop tests. Covers: default-deny matrix, exact file/dir scoping,
traversal & symlink fail-closed (incl. write-through leaving target
untouched), selected-directory grants issuing `dir` scope and actually
listing, rejection of file-scoped `list_dir` grants, confirmation
digest-binding / expiry / replay rejection, deny-default CLI (blank,
EOF, "yes" ≠ approve), provider-crash ⇒ deny, audit completeness +
0600 + tamper detection, durable `max_uses` across a broker rebuild,
concurrent thread/process requests and a daemon/CLI sharing one audit
log all respecting a single-use limit, two-writer audit locking,
fail-closed startup on a tampered/unreadable chain, private manifest
creation, refusal to chmod a symlinked manifest target, 0600 mode on
newly created write targets, daemon singleton locking, manifest
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

## Security corpus — PASS (all 10 criteria)

`python3 security/run_corpus.py` (200 scenarios + 50 injection twins, 0.5 s):

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
containing the old generic string `data`. Pyflakes is **clean** in the
pytest venv run. ShellCheck is clean locally under both 0.10 and 0.11
after fixing SC2012/SC2015 findings, and GitHub Actions PR #2 is green
(Python 3.11, Python 3.12 and ShellCheck jobs).

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
