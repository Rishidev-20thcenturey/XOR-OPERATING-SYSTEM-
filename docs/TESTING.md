# Testing guide

Everything below runs unprivileged, headless, offline, with stock
`python3` (≥3.9). `pytest` gives nicer output; `unittest` works too.

```sh
tools/test.sh        # unit tests + security corpus (the whole suite)
tools/validate.sh    # static checks: compile, pyflakes, XML/JSON/shell...
```

## Layers

| Layer | Where | What it proves |
|---|---|---|
| Policy/paths unit tests | `broker/tests/test_policy_and_paths.py` | default-deny matrix, scope exactness, traversal/symlink fail-closed, expiry/exhaustion, untrusted-text irrelevance |
| Broker end-to-end | `broker/tests/test_broker_e2e.py` | confirmation binding/staleness/replay, deny-by-default CLI, audit completeness + redaction + tamper detection, FD-anchored chain verification for `max_uses`, rejection of malformed/hash-invalid records and replaced/deleted/truncated logs, explicit `audit_failure` on append failure, two-writer locking, fail-closed startup verification, durable `max_uses` across a rebuild and across concurrent threads/processes/daemon+CLI sharing one audit log, selected-directory listing plus replacement-pin rejection, pinned-write replacement/deletion safety, write-failure rollback (existing content preserved, no temp-file litter), private `0700` staging (file created `0600`, destination mode applied inside it), staging-path substitution and source-symlink rejection, setup-failure identity-checked cleanup with substituted-entry preservation and unsafe-parent orphan preference, zero-progress/oversized `write()` results failing instead of looping, mode-bit preservation, symlink-target rejection, private manifest creation, symlinked/non-regular manifest refusal in both `init` and normal loading, daemon singleton locking, manifest rejection, real Unix-socket round-trip |
| Tokens, sandbox argv, grant editing | `broker/tests/test_tokens_sandbox.py` | private `0700` token store with `0600` files and rotation/revocation, path traversal rejection, missing/wrong/non-string token denial, unregistered-tool denial, revocation during confirmation blocking the write, malformed wire request audit, refusal to serve from a public socket directory or use a public token directory, sandbox argv contains no token and binds only the socket + token file, explicit network sharing, unsafe command/path/symlink/custom-bind rejection, atomic manifest grant/revoke with inode pinning and concurrent-writer serialization |
| Security corpus | `security/` (200 scenarios) | Milestone-1 acceptance criteria, below |
| Assistant gate | `assistant/tests/` | assistant disabled; declared surface read-only & broker-mapped; no direct file/network APIs in the stub |
| Viewer + Aurora cores | `desktop/tests/` | audit loading/filter/search/chain status and malformed-log fallback; XOR Shell app discovery/search, config sanitising/round-trip and malformed-config fallback, battery/network/memory parsers, wallpaper catalog, panel-opacity CSS and Control Centre text mode |
| Build script regression | `tools/test-build-iso.sh` | fake `lb` proves failure is not hidden by `tee`, package-check failure blocks the build, a stale ISO is never selected, and old artifacts are preserved |
| Package-list preflight | `tools/check-packages.py` | exact package names plus dependency-name closure against Debian trixie/updates/security indices (network required; not part of the offline suite) |
| Static validation | `tools/validate.py` | Python compile + pyflakes, corpus byte-deterministic, theme XML well-formed, `.desktop` keys/commands, QEMU identifiers, install-hook demo wrappers, shell `sh -n` + ShellCheck when installed, wallpaper/logo/CSS checks, live-build Aurora wiring, headless shell CLI |

## Debian package and ISO-build checks

`tools/test-build-iso.sh` runs as part of `tools/test.sh` and needs no
root, Debian host or network. It uses fake `lb`/`id`/`df` commands to
exercise the real build script's failure and artifact-freshness paths.

The package list itself is checked against live Debian indices by:

```sh
python3 tools/check-packages.py
```

This catches nonexistent/case-wrong names and missing dependency names
before a long image build. It is a name-level closure check; apt/lb
remains authoritative for versions, conflicts and architecture. The final
GitHub Actions ISO run (40737390213) built and checksum-verified the ISO,
passed the bounded QEMU/TCG live-session gate with a non-blank screenshot,
and installed the ISO to a 20 GiB virtio disk that booted again
(`result=PASS`, `install_result=PASS`, `second_boot_result=PASS`). The
live QEMU job boots the extracted kernel/initrd directly, and the install
job boots the installer kernel directly, so neither is a
firmware/bootloader menu test.

## The 200-scenario corpus

`security/generate_corpus.py` deterministically emits
`security/corpus/scenarios.json`: **100 out-of-grant** attempts,
**50 prompt-injection-style** scenarios, **50 legitimate** actions
(45 approve-intended, 5 deliberately user-denied).
`security/run_corpus.py` executes each against a fresh broker/sandbox
and checks expectations, audit records, hash chain, content redaction,
prompt-before-write, and side-effect-freeness of denied writes.
Injection scenarios additionally run a **twin** with the untrusted text
stripped and require an identical decision.

Honest scope: there is no LLM in the baseline. These tests prove the
*broker* treats document/tool-output text as decision-irrelevant; they
say nothing about how a future model behaves. That distinction is
deliberate.

### Milestone-1 acceptance criteria (checked by the runner)

1. Zero out-of-grant actions execute
2. Zero unauthorised network sends
3. Every executed write was confirmed first
4. Every request yields a valid, hash-chained audit record
5. No raw file content in audit logs
6. ≥95% of legitimate approved actions succeed
7. Unsupported ops reported as `denied_unsupported`, never faked
8. (extra) Untrusted text never changes a decision — twin check
9. (extra) Denied writes cause zero filesystem side effects
10. (extra) All scenario expectations met

Latest numbers, including environment and what did **not** run, live in
`docs/TEST_RESULTS.md`. Skips are reported as skips; we never edit a
test to hide a failure — failures get fixed in product code or the
expectation is corrected with a written justification.

## Not covered yet

* GTK interaction beyond the captured desktop: `confirm_gtk.py`,
  `xor_activity.py` GTK mode, XOR Shell and Control Centre are
  compile-checked and their non-GTK cores/headless CLIs are tested; the
  final ISO screenshot proves the Aurora panel/dock and wallpaper render,
  but on-desktop interaction (Esc denies, Deny focused, launcher
  search/focus, Wnck window buttons, Ctrl+1–4 activity filters, the
  Control Centre file chooser) is still a Milestone-2 checklist item —
  `xor-activity --text`, `xor-shell --status-json` and
  `xor-control-center --text` *are* exercised, and manifest grant/revoke
  is covered headlessly.
* Install-to-disk/second boot: `tools/test-install-qemu.sh` boots the
  installer kernel/initrd directly, installs to a virtio disk, then boots
  the installed disk. It is not a firmware/bootloader test. See
  `docs/TEST_RESULTS.md` for the latest Actions result; until a run is
  `PASS`, the installer path is implemented but unproven.
* Firmware/bootloader boot: the final cloud gate extracts the kernel/initrd
  and attaches the ISO as a CD-ROM. Boot the ISO through UEFI/OVMF and BIOS
  on a team machine before claiming the full bootloader path.
* Fuzzing the manifest/wire parsers, sustained concurrency/load (the
  audit log and `max_uses` have two-writer/concurrent-process tests, but
  the daemon remains single-threaded and has no rate limiting), and
  power-loss/fault-injection for the future updater (Milestones 3–4).
