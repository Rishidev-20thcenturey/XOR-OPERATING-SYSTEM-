# Roadmap

Status key: ✅ implemented & tested · 🧪 implemented, partly verified ·
📝 designed only · ⬜ not started. Detail in `docs/TEST_RESULTS.md`.

## Milestone 0 — Runnable OS foundation

* ✅ Repo layout, broker skeleton with one real scoped capability + audit
* ✅ Debian Live Build config (trixie/amd64/XFCE), install hook, LightDM autologin
* ✅ `tools/build-iso.sh`, `tools/run-qemu.sh` (fixed virtio target)
* ✅ ISO build + bounded QEMU/TCG live-session boot + OVMF/UEFI ISO boot
  verified in GitHub Actions run 40819648638; Secure Boot, firmware menus
  and BIOS ISO boot still unverified
* ✅ Install-to-disk + second boot verified in the same run (`result=PASS`,
  `install_result=PASS`, `second_boot_result=PASS`); interactive GTK QA
  still open

## Milestone 1 — Permission broker ✅ (acceptance criteria pass)

* ✅ Default-deny policy, exact file/dir scopes, expiry, use limits
* ✅ FD-anchored path enforcement (`O_NOFOLLOW`, anti traversal/symlink/race)
* ✅ Per-write, digest-bound, single-use confirmation (CLI provider)
* ✅ Hash-chained, content-free audit log + verify
* ✅ 200-scenario corpus + twin checks — all 10 criteria pass
* ✅ Unix-socket daemon + client; cooperating demo tools

## Milestone 2 — Integrated desktop 🧪

* ✅ Activity viewer core + `--text` mode on the real audit log
* 🧪 GTK confirmation dialog & GTK activity viewer: written to the
  keyboard contract (Deny focused, Esc denies, mnemonics, Ctrl+1–4),
  **need on-desktop verification** once the ISO boots
* 🧪 XOR Shell “Aurora”: glass panel, floating dock, searchable launcher,
  Command Centre and Control Centre implemented over XFCE/X11; core logic
  and headless CLIs are tested, GTK placement/focus/Wnck still need a booted ISO
* ✅ Royal Aurora wallpaper set, glass-X logo, shell/control menu entries,
  broker + Aurora autostart, visible demo
* 🧪 File-chooser grant flow in GTK Control Centre + atomic
  manifest grant/revoke; headless manifest tests pass, GTK interaction
  still needs on-desktop verification
* ⬜ Replace fallback XFCE panel only after Aurora passes daily-use,
  multi-monitor, focus and recovery-session checks

## Milestone 3 — Updater prototype + read-only assistant 📝/⬜

* 📝 A/B update design written (`UPDATE_DESIGN.md`); prototype + QEMU
  fault-injection suite are the next engineering milestone
* 🧪 Assistant: disabled stub + declared read-only, broker-mapped tool
  surface (tested disabled). **Gate**: only connect a model after the
  runner is sandbox-isolated and the corpus still passes; no cloud
  calls without a separate explicit decision. Local small-model
  evaluation notes go in an ADR before any download.

## Milestone 4 — Hardening & expansion 🧪

* 🧪 Opt-in bubblewrap launcher (`xor-broker launch`) with private
  namespaces, cleared environment and only broker socket + own token
  file; per-tool bearer tokens with revocation. Headless argv/token tests
  pass; no on-desktop bwrap run yet, and tokens are not bound to peer
  identity/sandbox instance.
* ⬜ Apply the sandbox to every tool/assistant and investigate Flatpak
  portals
* Manifest/wire-protocol fuzzing; rate limiting; audit anchoring
  (external head-hash witness) to strengthen tamper evidence
* Downgrade protection with monotonic counters (TPM investigation)
* Broader hardware testing beyond the QEMU virtio target
* Application packaging story for first-party tools

## Three most important next tasks

1. **Verify Secure Boot, firmware boot menus, BIOS ISO boot and broad
   hardware** on a team machine; the OVMF/UEFI ISO boot and the
   install-to-disk/second boot gates now pass in Actions.
2. **On-desktop Milestone-2 verification**: Aurora panel/dock/launcher,
   Command/Control Centre, GTK file chooser, GTK confirmation keyboard
   flow end-to-end through the daemon (deny default, Esc, timeout),
   activity viewer and demo launcher; capture screenshots and fix
   focus/placement issues.
3. **Isolation verification**: run `xor-demo-reader` under `xor-broker
   launch` on a booted ISO, confirm only the broker socket/token file are
   visible, and measure what breaks.
