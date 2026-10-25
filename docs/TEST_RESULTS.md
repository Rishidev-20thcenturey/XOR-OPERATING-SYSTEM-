# Test results — actual, from the dev sandbox session (2026-10-25)

Environment: Debian 12 sandbox, Python 3.11.2, 2 vCPU / 3.8 GiB,
**no QEMU, no /dev/KVM, no live-build, no Debian mirror access**
(egress limited to github/pypi/npm). Everything verifiable there was
run; the two things that could not run are listed as NOT RUN with the
exact blocker. Reproduce locally with `tools/test.sh`.

## Unit tests — PASS (35 tests)

`pytest broker/tests assistant/tests desktop/tests` → **35 passed**
(29 broker unit/E2E + 1 corpus-as-test + 3 assistant-gate + 2 viewer-core).
Covers: default-deny matrix, exact file/dir scoping, traversal &
symlink fail-closed (incl. write-through leaving target untouched),
confirmation digest-binding / expiry / replay rejection, deny-default
CLI (blank, EOF, "yes" ≠ approve), provider-crash ⇒ deny, audit
completeness + 0600 + tamper detection, manifest strictness
(unsupported ops ungrantable), real Unix-socket round-trip, assistant
disabled + read-only surface, activity filters/search/chain.

Two failures occurred during development and were fixed in **product
code** (symlinked-dir `ENOTDIR` now maps to fail-closed
`PathEscapeError`) and in one **test** (tamper test originally set a
field to its existing value). No expectation was weakened.

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

`tools/validate.py`: 28 Python files compile, **pyflakes clean**,
example manifest valid, corpus regenerates byte-identically, 3 XFCE
XML files well-formed, 3 `.desktop` files sane, live-build tree
complete, 9 shell scripts parse, wallpaper 1920×1080.
*SKIPPED in sandbox:* shellcheck (not installed; runs in CI instead).

## Demo executions — PASS

* `xor-broker demo`: read granted ✓ executed · sibling secret ✗
  denied_policy · traversal ✗ denied_policy · confirmed write ✓ ·
  network ✗ denied_unsupported; 5 audit records, chain valid.
* `xor-broker init` + `grants` in a throwaway HOME: demo grants created exactly as documented.

## NOT RUN — blocked by sandbox, commands ready

| Item | Blocker (verbatim) | Run instead |
|---|---|---|
| ISO build | `ERROR: Debian Live Build ('lb') not found.` (no lb, no mirror, unprivileged) | On a Debian/Ubuntu machine: `sudo apt install live-build && sudo tools/build-iso.sh` |
| QEMU boot | `ERROR: qemu-system-x86_64 not found.` (also no /dev/KVM) | `sudo apt install qemu-system-x86 ovmf && tools/run-qemu.sh` |
| GTK UI behaviour | no display / no PyGObject | After first ISO boot: run `xor-demo-writer`, verify Deny-focused dialog, Esc denies, 120 s timeout; open XOR Activity, check Ctrl+1–4 / Ctrl+F / Esc. Code is compile-checked; `--text` mode is tested. |

Consequence, stated plainly: **Milestone 0's "bootable image" is
configured and scripted but not yet boot-verified.** Milestone 1
(broker) acceptance is fully demonstrated. First team-machine task:
build, boot, and replace this section's NOT RUN rows with evidence.
