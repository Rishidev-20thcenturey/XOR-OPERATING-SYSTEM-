# XOR OS product plan — from proven baseline to complete OS

XOR becomes a “complete OS” by shipping and testing gates in order, not
by declaring it finished when it looks beautiful. The permission broker
remains the product’s security centre at every stage.

## Definition of complete

A release can honestly call itself the complete XOR OS when:

1. It builds reproducibly enough to publish an ISO, checksum and build manifest.
2. It boots in the fixed QEMU target and installs onto a virtual disk.
3. A user can complete first-run setup by keyboard or pointer.
4. The XOR Shell, Control Centre, Activity and broker work on the booted system.
5. Core daily apps launch, open/save files and respect declared permissions.
6. Updates can be applied, verified and rolled back after fault injection.
7. Recovery is documented and tested.
8. At least one real-hardware smoke test is recorded.
9. Project claims in README/release notes link to actual test evidence.

## Phase 0 — Foundation ✅

* Debian trixie/XFCE image configuration, build/run scripts.
* Typed, default-deny broker; scoped FD-anchored file access.
* Per-write confirmations; hash-chained local audit.
* 200-scenario security corpus, all acceptance criteria passing.

## Phase 1 — Prove the machine boots

**Goal:** ISO built on a team/CI machine and booted in QEMU.

* Run `sudo tools/build-iso.sh`; fix live-build or package failures.
* Boot with `tools/run-qemu.sh`; capture serial log and screenshot.
* Verify LightDM autologin, XFCE fallback, broker daemon, XOR Shell autostart.
* Repeat a second boot from the virtual disk/live environment.
* Record outcomes in `docs/TEST_RESULTS.md` before changing any claims.

**Exit gate:** Boot-to-desktop evidence + broker demo via GTK dialog.

**Status (2026-10-29):** ISO build, direct-kernel QEMU boot, OVMF/UEFI
ISO boot, install-to-disk and second boot passed in Actions run
40819648638. Secure Boot, firmware menus, BIOS ISO boot, broad hardware
and interactive GTK QA remain open.

## Phase 2 — XOR Shell “Aurora”

**Now implemented as a preview layer over XFCE/X11:**

* Glass top panel, floating dock, full-screen app launcher.
* Command Centre and first-party Control Centre.
* App discovery/search, categories, pinned apps, workspaces and live windows via optional Wnck.
* Accent palettes, wallpaper picker, dock sizing, opacity and reduce-motion setting.
* Premium Royal Aurora wallpaper, XOR logo and unified CSS design tokens.

**Remaining inside Phase 2:**

* Daily-use test on a booted desktop, then fix placement/focus/multi-monitor issues.
* Super-key/global shortcuts through the session settings.
* Notification aggregation, window previews and richer menu actions.
* File-chooser capability-grant journey from launcher/Control Centre
  (implemented; verify keyboard-only GTK flow on a booted ISO).
* Decide when XFCE panel can be removed; retain a recovery session.

**Exit gate:** Keyboard-only launcher/control/confirmation flows pass on a booted ISO.

## Phase 3 — Product applications and first-run

* XOR Welcome/first-run, guided broker demo and recovery keys education.
* Security Centre view inside Control Centre: grants, revoke/expiry controls.
* Files integration: “Open/share with a tool” capability grants for selected files only.
* Software presentation using Debian packages; no new package manager.
* Curated default apps, MIME defaults, accessibility pass and localisation groundwork.
* Installer path validation (live installer or Debian installer decision via ADR).

**Exit gate:** A non-developer can install, sign in, use core apps and understand a permission prompt without reading source code.

## Phase 4 — Recoverable updates

Implement `docs/UPDATE_DESIGN.md`:

* Inactive-slot writes, manifest hashing, offline signing/verification.
* One-shot boot flag, health checks, mark-good and automatic rollback.
* Downgrade policy based on a monotonic version counter, not local clock.
* QEMU fault injection for power loss, corruption, bad signature and failed health check.

**Exit gate:** Signed update accepted; every injected failure boots or returns to the known-good slot.

## Phase 5 — Isolated read-only assistant

* Bubblewrap-isolated runner with no home-directory bind mounts.
* Authenticated tool identity tied to sandbox instance, not self-declared names.
* Broker socket as the only path to resources; network denied at both broker and sandbox layers.
* Read-only tools only, model choice and offline/online policy recorded in an ADR.
* Re-run 200-scenario corpus plus assistant-specific adversarial scenarios.

**Exit gate:** Assistant can read only individually granted resources and cannot execute denied, write or external-send actions.

## Phase 6 — Hardening and hardware

* Manifest/wire fuzzing, load tests and rate limiting.
* External audit-head anchoring and log rotation with chain preservation.
* TPM/secure-boot investigation; broader hardware matrix.
* Performance and battery profiling; migration path from GTK3/X11 shell to GTK4/Wayland layer-shell when justified.

## Design pillars

* **Lavish, not noisy:** one visual language, high contrast, motion with a reduce-motion escape.
* **Beautiful controls tell the truth:** no fake toggles or simulated permissions.
* **XFCE is the safety net, not the identity:** XOR replaces surfaces only as each one proves itself.
* **Every milestone ends in evidence:** tests, logs, screenshots or a signed build manifest.
