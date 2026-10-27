# Test results — actual, from available sandboxes (2026-10-25; hardening passes 2026-10-27)

Original baseline environment: Debian 12 sandbox, Python 3.11.2,
2 vCPU / 3.8 GiB, **no QEMU, no /dev/kvm, no live-build, no Debian
mirror access, no GTK/PyGObject** (egress limited to github/pypi/npm).
The 2026-10-27 hardening pass was re-run on Termux/Android with Python
3.13.13 under the same unprivileged, headless constraints, because the
Debian sandbox was unavailable. The final run in this document is from
**2026-10-27 21:14 +0530** on that environment. Debian package indices
were reachable for the name/dependency preflight; an actual Debian
`lb build` still could not run (no live-build/root Debian host).
Everything verifiable in each environment was run; what could not run is
listed as NOT RUN with the exact blocker. Reproduce locally with
`tools/test.sh`.

## Unit tests — PASS (87 tests)

Latest `tools/test.sh` run (unittest fallback) → **87 passed**:
73 broker tests (including corpus-as-test), 3 assistant-gate tests and
11 desktop tests. Exact durations from the final run: 73 tests in
3.316 s, 3 tests in 0.000 s, 11 tests in 0.040 s. Covers: default-deny
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
on newly created write targets, pinned-write safety (normal write
preserving inode and mode, replacement not truncated, deleted target not
recreated, target-write failure and zero-progress rollback restoring the
original, explicit rollback-failure reporting), unpinned write failure
preservation and temp-file cleanup, zero-progress/oversized `write()`
rejection, mode-bit preservation, symlink-target rejection, setuid/setgid
bits cleared on replacement, daemon singleton locking, manifest
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

This release-readiness pass fixed: `write_file` opened a pinned target
with `O_TRUNC` *before* checking its inode pin, so replacing the selected
file after approval destroyed the replacement; write opens now omit
`O_TRUNC`, check the opened inode, and only then change content. A vanished
pinned path is not recreated. It also fixed `build-iso.sh` masking a
failed `lb build` through `tee` and potentially selecting a stale ISO,
and corrected four invalid Debian package names (`Thunar-volman`,
`fonts-Inter`, `fonts-DejaVu`, `policykit-1`). Regression tests cover
normal/new/symlink/failed writes and the replaced/deleted pinned-write
cases, plus the build failure/stale-artifact paths. No security
expectation was weakened.

The write-safety follow-up made write failures non-destructive in the
reported-error case. Unpinned writes now stage bytes in a private
same-directory temp file, fsync it, re-check that the target is still the
same regular file (never a symlink), and rename it into place; a failed
write, fsync or rename leaves the original file unchanged and removes the
temp file. Pinned writes keep their inode identity: new bytes are staged,
old bytes backed up, and the target is overwritten from offset zero and
truncated to the new length only after all new bytes are accepted. A
target-write failure restores the backup; a failed rollback is reported
explicitly rather than masked as success. Zero-progress and
oversized `os.write()` results now fail explicitly instead of looping or
being silently accepted. Tests cover unpinned
failure preservation, temp cleanup, zero/oversized results, mode
preservation (including setuid/setgid clearing), symlink rejection, pinned
inode/mode preservation, and pinned rollback on write failure and zero
progress, including explicit reporting when rollback itself fails.
`tools/test.sh` exited 0; the corpus still passed all 10 criteria. Not
crash-atomic: a crash during a pinned in-place update can still leave
partial content, unpinned replacement does not preserve ownership/ACLs/
xattrs or hard links, and rollback needs read permission on the pinned
file.

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

`tools/validate.py`: 35 Python files compile, example manifest valid,
corpus regenerates byte-identically, 3 XFCE XML files well-formed,
7 `.desktop` entries and Aurora commands sane, live-build tree and
Aurora install wiring complete, package list uses the corrected Debian
names, QEMU device identifiers and install-hook demo wrappers correct,
11 shell scripts parse and **ShellCheck 0.11.0 is clean**, legacy PNG
1920×1080, CSS classes/braces, SVG logo, Royal Aurora JPEG and XFCE
default agree, and headless XOR Shell/Control Centre commands run. The
corpus's unsupported-operation payload marker was made path-independent
so the content-redaction check cannot false-positive on a sandbox path
containing the old generic string `data`. Pyflakes was **not installed**
in the final Termux run, so `tools/validate.py` reported it as
`SKIPPED`; no pyflakes-clean claim is made. Earlier Debian/pytest-venv
runs and GitHub Actions PR #2 (Python 3.11, Python 3.12 and ShellCheck
jobs) were green historically, but were not re-run here.

## Debian package list and build-script checks — PASS (no ISO build)

`python3 tools/check-packages.py` fetched the configured trixie,
trixie-updates and trixie-security indices for `main`, `contrib` and
`non-free-firmware` and reported:

```
PASS: 40 requested packages found; 69222 package names across 71300 records;
dependency names resolve.
```

This is a name-level dependency closure; apt/lb remains authoritative
for versions, conflicts and architecture. A negative control with
`Thunar-volman` exited 1 and suggested `thunar-volman`.

`tools/test-build-iso.sh` (part of `tools/test.sh`, fake `lb`/`id`/`df`,
no root or network) passed four cases: failed `lb build` returns failure
through the `tee` pipeline; failed package validation blocks the build;
a successful build selects the fresh ISO, not a future-dated stale ISO;
and success-with-no-new-ISO exits failure. The stale ISO is preserved in
all cases; nothing is silently deleted.

An actual `lb build` was **not** run, so no ISO-build or boot claim is
made. The fake-lb test proves script control flow only, not that Debian
live-build succeeds.

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
| ISO build | `ERROR: Debian Live Build ('lb') not found.` (no live-build/root Debian host; package indices *were* reachable for the separate name check) | On a Debian/Ubuntu machine: `sudo apt install live-build && sudo tools/build-iso.sh` |
| QEMU boot | `ERROR: qemu-system-x86_64 not found.` (also no /dev/kvm) | `sudo apt install qemu-system-x86 ovmf && tools/run-qemu.sh` |
| GTK UI behaviour | no display / no PyGObject | After first ISO boot: verify Aurora panel/dock/launcher/Command Centre/Control Centre, Wnck window buttons and workspaces; run `xor-demo-writer`, verify Deny-focused dialog, Esc denies, 120 s timeout; open XOR Activity, check Ctrl+1–4 / Ctrl+F / Esc. Code is compile-checked; non-GTK cores and text/status modes are tested. |

Consequence, stated plainly: **Milestone 0's "bootable image" is
configured and scripted but not yet boot-verified, and Aurora is a
tested-core preview rather than a daily-used shell.** Milestone 1
(broker) acceptance is fully demonstrated. First team-machine task:
build, boot, exercise Aurora + GTK confirmation, and replace this
section's NOT RUN rows with evidence.
