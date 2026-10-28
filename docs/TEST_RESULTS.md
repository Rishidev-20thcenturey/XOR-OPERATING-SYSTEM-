# Test results — actual, from available sandboxes and GitHub Actions (2026-10-25 to 2026-10-28)

Original baseline environment: Debian 12 sandbox, Python 3.11.2,
2 vCPU / 3.8 GiB, **no QEMU, no /dev/kvm, no live-build, no Debian
mirror access, no GTK/PyGObject** (egress limited to github/pypi/npm).
The 2026-10-27/28 hardening pass was re-run on Termux/Android with Python
3.13.13 under the same unprivileged, headless constraints, because the
Debian sandbox was unavailable. Debian package indices were reachable for
the name/dependency preflight; an actual Debian `lb build` ran in the
GitHub Actions Debian trixie container, not on Termux. Everything
verifiable in each environment was run; what could not run is listed as
NOT RUN with the exact blocker. Reproduce locally with `tools/test.sh`.
The latest ISO-build/QEMU evidence is from GitHub Actions run
[40597398794](https://github.com/Rishidev-20thcenturey/XOR-OPERATING-SYSTEM-/actions/runs/40597398794),
described in the ISO-build/QEMU section below.

## Unit tests — PASS (110 locally on 2026-10-28; 96 in earlier CI)

The 2026-10-28 local Termux run passed **110 tests**: **94 broker tests**
(including the new token/sandbox/grant regression file) and **16
assistant/desktop tests**. Static validation passed, including pyflakes,
ShellCheck, the 200-scenario corpus + 50 injection twins, and all 10
corpus acceptance criteria. The new tests cover private token-store
modes/rotation/revocation, missing/wrong/non-string token denial,
unregistered-tool denial, revocation during confirmation, malformed-wire
audit, refusal to serve from a public socket directory, sandbox argv
token non-disclosure, socket/token-only binds, unsafe-path rejection,
and atomic manifest grant/revoke with inode pinning.

Earlier PR CI run [40640029964](https://github.com/Rishidev-20thcenturey/XOR-OPERATING-SYSTEM-/actions/runs/40640029964) passed **96 tests**: 81 broker tests and 15 assistant/desktop tests. Both Python 3.11 and 3.12 jobs passed. Static validation, ShellCheck, build-script artifact regressions, and the 200-scenario corpus + 50 injection twins also passed; all 10 corpus acceptance criteria passed. The dangling audit-log symlink regression is included.

Earlier local Termux `tools/test.sh` run (unittest fallback) → **95 passed**: 80 broker tests (including corpus-as-test), 3 assistant-gate tests and 12 desktop tests. Exact durations from that pre-regression run: 80 tests in 3.338 s, 3 tests in 0.003 s, and 12 tests in 0.040 s. Covers: default-deny
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
preservation and staging cleanup, private `0700` staging (file created
`0600`, destination mode applied inside it),
staging-path symlink substitution not redirecting the FD-anchored rename,
staged-source symlink replacement rejection, setup-failure cleanup that
preserves substituted directories and symlinks and removes only the exact
created directory, orphan preference in unsafe shared parents,
zero-progress/oversized
`write()` rejection, mode-bit preservation, symlink-target rejection,
setuid/setgid bits cleared on replacement, daemon singleton locking,
manifest
strictness (unsupported ops ungrantable), real Unix-socket round-trip,
assistant disabled + read-only surface, activity filters/search/chain
(and malformed-log fallback), and the Aurora shell core (desktop-entry
parsing/search/grouping, launch argv field-code stripping, config
sanitising/round-trip and malformed-config fallback, battery/network/
GiB memory parsing, wallpaper catalog, panel-opacity CSS generation,
Control Centre text status, and connector-named XFCE 4.20 backdrop
application with an `xfdesktop --reload`).

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
reported-error case. Unpinned writes now stage bytes in a private staging
directory in the target's parent, fsync the staged file, re-check that
both the staged source and the target are still the expected regular files
(never symlinks), and rename into place; a failed write, fsync or rename
leaves the original file unchanged and removes the staging files. Pinned
writes keep their inode identity: new bytes are staged,
old bytes backed up, and the target is overwritten from offset zero and
truncated to the new length only after all new bytes are accepted. A
target-write failure restores the backup; a failed rollback is reported
explicitly rather than masked as success. Zero-progress and
oversized `os.write()` results now fail explicitly instead of looping or
being silently accepted. Tests cover unpinned
failure preservation, staging cleanup, zero/oversized results, mode
preservation (including setuid/setgid clearing), symlink rejection, pinned
inode/mode preservation, and pinned rollback on write failure and zero
progress, including explicit reporting when rollback itself fails.
`tools/test.sh` exited 0; the corpus still passed all 10 criteria. Not
crash-atomic: a crash during a pinned in-place update can still leave
partial content, unpinned replacement does not preserve ownership/ACLs/
xattrs or hard links, and rollback needs read permission on the pinned
file.

The staging-race follow-up hardened unpinned writes further: staging now
uses an owner-verified `0700` directory created inside the target's parent
and anchored to the already-opened parent FD, so it stays on the same
filesystem. The staged file is created `0600`; the destination rwx mode is
applied while it is still inside the private directory, so confidentiality
comes from the `0700` directory. The final `rename` uses the
held staging FD rather than the staging pathname, and the staged inode is
re-checked immediately before rename. Tests assert the private
directory/file modes and inspect staged bytes before rename, swap the
staging directory name with a symlink (the write still uses the held FD and
the attacker's file is untouched), and replace the staged file with a
symlink (rejected, target unchanged). No separate-UID process is available
in this sandbox, so confidentiality is demonstrated by mode/owner
enforcement rather than by dropping privileges. Residual races: same-UID
processes can enter the `0700` directory (outside the broker's cross-user
boundary); a same-UID source swap after the final check remains
theoretically possible; another user with write access to a non-sticky
parent can rename the staging entry, causing a reported failure or an
empty-directory leftover, but cannot read or substitute the staged bytes
because the held directory remains `0700`; and a last-instant destination
replacement between the final target check and `rename` remains.

The staging-cleanup follow-up captured the staging directory identity
immediately after `mkdir` and made setup-failure cleanup identity-based:
the entry is removed only while it still names that exact directory, is a
directory, is owned by this effective UID, and the parent is safe from
cross-user renames. A substituted entry is left untouched. Tests simulate
an entry swap during setup (the replacement survives and the original is
left orphaned), a symlink substitution (left in place, target untouched),
ordinary setup failure (the exact created directory is removed), and a
group/other-writable non-sticky parent (the empty directory is left rather
than risking deletion of another entry). The check-to-`rmdir`
window is not atomically closable portably; in unsafe shared parents the
broker therefore prefers the orphan. A microscopic residual
remains if parent permissions change between the safety check and `rmdir`,
and same-UID processes remain outside the cross-user boundary.

The final live-session pass fixed XFCE 4.20 backdrop ownership. XFCE 4.20
keys backdrops by connector name (in QEMU: `monitorVirtual-1`), not by the
older `monitor0`/`monitor1` indexes, so the skel defaults were ignored and
the desktop showed Debian’s default wallpaper. The shell now discovers
connected connectors with `xrandr`, writes both the legacy and
connector-named single-image/workspace keys, sets `image-show` and
`image-style`, marks the skel backdrop as already migrated, and asks the
running xfdesktop to reload. The shell’s own startup applies this after
XFCE’s first-login migration. The QEMU job now waits 240 seconds after the
graphical signal, retries a blank framebuffer dump, and fails its final gate
unless a non-blank screenshot was captured. The final screenshot is real:
1280×800, 793,977 bytes, showing the Aurora panel/dock and Royal Aurora
wallpaper. No security expectation was changed.

Earlier live-session runs were used to isolate the failures and are not the
latest evidence: 40542700041 was `INCONCLUSIVE`; 40585123362 reached the
graphical target but showed Debian’s default wallpaper; 40588316194 passed
the serial gate but captured a 282-byte black screenshot; 40590709471
confirmed the desktop after a longer wait; and 40597398794 is the accepted
final run.

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

`tools/validate.py`: 36 Python files compile, **pyflakes is clean**,
example manifest valid, corpus regenerates byte-identically, 3 XFCE XML
files well-formed, 7 `.desktop` entries and Aurora commands sane,
live-build tree and Aurora install wiring complete, package list uses the
corrected Debian names, QEMU device identifiers and install-hook demo
wrappers correct, 11 shell scripts parse and **ShellCheck 0.11.0 is
clean**, legacy PNG 1920×1080, CSS classes/braces, SVG logo, Royal Aurora
JPEG and XFCE default agree, and headless XOR Shell/Control Centre
commands run. The corpus’s unsupported-operation payload marker was made
path-independent so the content-redaction check cannot false-positive on a
sandbox path containing the old generic string `data`.

## Debian package list and build-script checks — PASS (no local ISO build)

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

An actual `lb build` was **not** run in the Termux sandbox, so the fake-lb
claim is limited to script control flow. The final GitHub Actions ISO build
in run 40597398794 did run the real Debian Live Build and succeeded.

## GitHub Actions ISO build and QEMU live-session gate — 2026-10-27 (run 40597398794)

Commit under test: `26e87fdddc8016ece8cadb0d24d1d971d1ba9eb3` on
`review/ac48e68-hardening`.

Workflow: `.github/workflows/iso-build.yml`, job
`Build ISO and attempt QEMU boot`, run
[40597398794](https://github.com/Rishidev-20thcenturey/XOR-OPERATING-SYSTEM-/actions/runs/40597398794).
The run was created `2026-10-27T22:46:57Z`, completed
`2026-10-27T23:03:32Z` (16m32s) with conclusion **`success`**. The final
gate passed because the build succeeded, QEMU returned `PASS`, and a
non-blank screenshot was captured.

Runner/build stage:
* Build finished `2026-10-27T22:56:19Z`.
* Debian Live Build `20250505+deb13u1` built
  `live-image-amd64.hybrid.iso`; the log ends
  `P: Build completed successfully`.
* ISO size: 1,464,860,672 bytes (1.364 GiB).
* ISO SHA-256: `2fb52d345a9209fae87a32965f32d64e5fcb75ce2ad207dd895d79034ed80603`.
* `BUILD-MANIFEST.txt` SHA-256:
  `5d5ae9f95c766969a1f6f175c3ddb0c1bdd5fdc6f4911bfd40139236967634ac`.
* Package-list SHA-256:
  `e1925634ada56b085d2df26e8c45f436358867de11e9598549a2da56b2a1741f`.
* Disk after build: `/dev/root ext4 145G total, 67G used, 78G available
  (47%)`.
* The manifest records `git_revision 26e87fdddc8016ece8cadb0d24d1d971d1ba9eb3
  (uncommitted changes: 39 files)`. The local repository worktree was clean
  after the push (`git status --short` empty); this report records the
  manifest’s dirty-state count as an open reproducibility caveat rather than
  attributing it to a named source change.
* Build evidence artifact `xor-build-evidence-40597398794` is 247,447
  bytes and contains `lb-build.log`, `build-summary.txt`, the checksum and
  the manifest.

QEMU stage:
* QEMU/TCG boot of the extracted Debian kernel/initrd
  (`6.12.113+deb13-amd64`) with the ISO attached as a CD-ROM. The virtual
  target is q35, 2 vCPU, 4 GiB RAM, virtio-vga and serial logging; KVM was
  unavailable, so this is TCG. The workflow’s direct-kernel path is not a
  firmware/bootloader test.
* Result file: `result=PASS`, `graphical_signal=1`, `linux_signal=0`,
  `qemu_exit=0`, `screenshot_ok=1`.
* Accepted serial signal:
  `[   85.036916] systemd[1]: Started lightdm.service - Light Display Manager.`
  LightDM autologin then started `xfce4-session`; the pinned desktop
  screenshot was captured after the longer post-signal wait and retry
  logic.
* Screenshot: PNG 1280×800, 8-bit sRGB, **793,977 bytes**. Visual
  inspection shows the XOR Aurora top panel (launcher, workspaces, clock,
  network/power pills, Control Centre, power), the floating dock, and the
  **Royal Aurora / XOR wallpaper**. XFCE remains visible as the fallback
  layer by design.
* The final gate fails if QEMU is not exactly `PASS` or the screenshot is
  blank/too small. The screenshot artifact is real visual evidence, not a
  serial-only claim.
* QEMU evidence artifact `xor-qemu-evidence-40597398794` is 1,604,004,923
  bytes and contains `serial.log`, `screenshot.png`, `qemu-result.txt`,
  `boot-inputs.txt`, `filesystem.packages`, `screendump.log` and the
  extraction log. The ISO artifact `xor-iso-image-40597398794` is
  1,455,998,922 bytes.

Not verified in this run:
* Firmware/UEFI/BIOS bootloader selection (the kernel/initrd were extracted
  and booted directly; the ISO was attached as a CD-ROM).
* Interactive GTK QA beyond the captured desktop: launcher focus/search,
  Wnck window buttons, Control Centre actions, broker GTK confirmation
  dialogs and Ctrl+1–4 Activity filters still require a human test on a
  booted machine.
* KVM acceleration and broad hardware. This is a bounded TCG smoke test.

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
| ISO build | Passed in GitHub Actions run 40597398794; see the ISO-build/QEMU section above for exact checksum and artifact evidence. | Local `sudo tools/build-iso.sh` remains available for a team-machine rebuild. |
| Install-to-disk + second boot | The installer path, preseed and `tools/test-install-qemu.sh` gate are implemented, but no successful Actions run has been recorded yet. | Run the `install-to-disk` job in the ISO workflow and record the artifact/result here. |
| Firmware/bootloader boot | The accepted QEMU gate extracts the kernel/initrd and attaches the ISO as a CD-ROM; it does not select a BIOS/UEFI bootloader. | Boot the ISO through firmware (UEFI/OVMF and BIOS) on a team machine and confirm the live session. |
| GTK interaction | The final screenshot shows the Aurora desktop on the live ISO, but there is still no unattended interactive test for launcher focus/search, Wnck window buttons, Control Centre actions, broker GTK confirmation or Activity filters. | On the booted ISO: verify the Aurora panel/dock/launcher/Command Centre/Control Centre; run `xor-demo-writer`, verify the Deny-focused dialog, Esc denies and the 120 s timeout; open XOR Activity and check Ctrl+1–4 / Ctrl+F / Esc. Non-GTK cores and headless text/status modes are tested. |

Consequence, stated plainly: the ISO builds, is checksummed and
artifact-backed, and the bounded QEMU/TCG direct-kernel boot now reaches a
live XFCE session with the Aurora shell and Royal Aurora wallpaper visibly
confirmed. That is a real live-session pass, not a daily-use claim:
firmware/bootloader selection, interactive GTK QA and broad hardware
verification remain open. The first team-machine task is to boot the ISO
through firmware and exercise Aurora and broker confirmations interactively.
