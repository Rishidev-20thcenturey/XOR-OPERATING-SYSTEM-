# Test results — actual, from available sandboxes and GitHub Actions (2026-10-25 to 2026-10-27)

Original baseline environment: Debian 12 sandbox, Python 3.11.2,
2 vCPU / 3.8 GiB, **no QEMU, no /dev/kvm, no live-build, no Debian
mirror access, no GTK/PyGObject** (egress limited to github/pypi/npm).
The 2026-10-27 hardening pass was re-run on Termux/Android with Python
3.13.13 under the same unprivileged, headless constraints, because the
Debian sandbox was unavailable. The final run in this document is from
**2026-10-27 21:37 +0530** on that environment. Debian package indices
were reachable for the name/dependency preflight; an actual Debian
`lb build` still could not run (no live-build/root Debian host).
Everything verifiable in each environment was run; what could not run is
listed as NOT RUN with the exact blocker. Reproduce locally with
`tools/test.sh`. The latest ISO-build/QEMU evidence is from GitHub
Actions run 40542700041, described in the ISO-build/QEMU section below.

## Unit tests — PASS (94 tests)

Latest `tools/test.sh` run (unittest fallback) → **94 passed**:
80 broker tests (including corpus-as-test), 3 assistant-gate tests and
11 desktop tests. Exact durations from the final run: 80 tests in
2.422 s, 3 tests in 0.000 s, 11 tests in 0.031 s. Covers: default-deny
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
claim is limited to script control flow. The GitHub Actions ISO build in run
40542700041 did run the real Debian Live Build and succeeded.

## GitHub Actions ISO build and QEMU smoke test — 2026-10-27 (run 40542700041)

Commit under test: `d30c7a6045ef00437dbbef4fbad6145f6ad5bb0a` on
`review/ac48e68-hardening`.

Workflow: `.github/workflows/iso-build.yml`, job
`Build ISO and attempt QEMU boot`, run
[40542700041](https://github.com/Rishidev-20thcenturey/XOR-OPERATING-SYSTEM-/actions/runs/40542700041).

The job ran on `ubuntu-latest` and completed with conclusion `failure`.
The ISO build itself succeeded; the final gate failed because the QEMU
boot check returned **`INCONCLUSIVE`**, not `PASS`.

Runner disk state: 86 GiB available before the job. The workflow’s 20 GiB
reclaim threshold was not reached, so no preinstalled toolchain was
removed.

Build stage:
* Started `2026-10-27T16:37:58Z`, completed `2026-10-27T16:47:01Z`
  (about 9m03).
* Debian Live Build `20250505+deb13u1` built
  `live-image-amd64.hybrid.iso`.
* ISO size: 1,464,860,672 bytes (1.364 GiB).
* ISO SHA-256: `34a44ba9fbc7bb692f6ea998740639f7651463b1327ba36cd8726ce20f837a83`.
* Build checksum verification: `live-image-amd64.hybrid.iso: OK`.
* Build log, checksum and `BUILD-MANIFEST.txt` were uploaded as artifact
  `xor-build-evidence-40542700041`.

QEMU stage:
* Started `2026-10-27T16:47:07Z`, completed `2026-10-27T17:17:15Z`
  (about 30m08). The signal wait is a bounded 30-minute loop.
* Final workflow result: `INCONCLUSIVE`; the final gate failed with
  “`QEMU result was 'INCONCLUSIVE', not PASS`”.
* The checked signal set included
  `Reached target Graphical Interface`, `Started LightDM` and
  `Started lightdm.service`. No accepted reliable boot-to-live-session
  signal was produced within the bounded wait.
* QEMU/TCG and evidence-handling tools: QEMU 8.2.2, xorriso 1.5.6.
* The final QEMU result was not accepted, so no GUI/screenshot claim was
  made and the final gate failed rather than passing an inconclusive
  check.

Artifacts from the failed-but-evidence-collecting run:
* `xor-build-evidence-40542700041` (248,303 bytes)
* `xor-iso-image-40542700041` (1,455,997,053 bytes; actual ISO was
  1,464,860,672 bytes)
* `xor-qemu-evidence-40542700041` (1,602,011,892 bytes)

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
| ISO build | Now run successfully in GitHub Actions run 40542700041; see the new ISO-build/QEMU section above for build, checksum and artifact evidence. | Local `sudo tools/build-iso.sh` remains available for a team-machine rebuild. |
| QEMU boot | No reliable boot-to-live-session signal was accepted in GitHub Actions run 40542700041; result `INCONCLUSIVE`, final gate failed. | Keep the bounded serial/TCG smoke test, inspect the captured QEMU evidence, and retry the live-session gate. |
| GTK UI behaviour | No display / no PyGObject | After first ISO boot: verify Aurora panel/dock/launcher/Command Centre/Control Centre, Wnck window buttons and workspaces; run `xor-demo-writer`, verify Deny-focused dialog, Esc denies, 120 s timeout; open XOR Activity, check Ctrl+1–4 / Ctrl+F / Esc. Code is compile-checked; non-GTK cores and text/status modes are tested. |

Consequence, stated plainly: the ISO now builds and is checksummed and
artifact-backed, but the **QEMU boot-to-live-session gate was not passed**
in the 2026-10-27 cloud run; Aurora remains a tested-core preview rather
than a daily-used shell. The first team-machine task is to retry the QEMU
live-session signal and then exercise Aurora + GTK confirmation.
