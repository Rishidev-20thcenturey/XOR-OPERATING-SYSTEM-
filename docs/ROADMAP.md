# Roadmap

Status key: ✅ implemented & tested · 🧪 implemented, partly verified ·
📝 designed only · ⬜ not started. Detail in `docs/TEST_RESULTS.md`.

## Milestone 0 — Runnable OS foundation

* ✅ Repo layout, broker skeleton with one real scoped capability + audit
* ✅ Debian Live Build config (trixie/amd64/XFCE), install hook, LightDM autologin
* ✅ `tools/build-iso.sh`, `tools/run-qemu.sh` (fixed virtio target)
* 🧪 ISO **build + QEMU boot unverified in the dev sandbox** → first task
  on a team machine; scripts fail with clear guidance until then

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
* ✅ Theme, wallpaper, menu entries, broker autostart, visible demo
* ⬜ File-chooser grant flow in GTK (library support exists:
  `manifest.grant_for_selected_file` with inode pinning)
* ⬜ Panel branding (default XFCE panel kept on purpose, see theme README)

## Milestone 3 — Updater prototype + read-only assistant 📝/⬜

* 📝 A/B update design written (`UPDATE_DESIGN.md`); prototype + QEMU
  fault-injection suite are the next engineering milestone
* 🧪 Assistant: disabled stub + declared read-only, broker-mapped tool
  surface (tested disabled). **Gate**: only connect a model after the
  runner is sandbox-isolated and the corpus still passes; no cloud
  calls without a separate explicit decision. Local small-model
  evaluation notes go in an ADR before any download.

## Milestone 4 — Hardening & expansion ⬜

* Tool/assistant process isolation (bubblewrap profiles; Flatpak portal
  investigation) and authenticated tool identity for the broker socket
* Manifest/wire-protocol fuzzing; rate limiting; audit anchoring
  (external head-hash witness) to strengthen tamper evidence
* Downgrade protection with monotonic counters (TPM investigation)
* Broader hardware testing beyond the QEMU virtio target
* Application packaging story for first-party tools

## Three most important next tasks

1. **Build & boot the ISO on a real dev machine** (`sudo tools/build-iso.sh`,
   `tools/run-qemu.sh`); fix live-build fallout; record evidence + screenshots.
2. **On-desktop Milestone-2 verification**: GTK confirmation keyboard
   flow end-to-end through the daemon (deny default, Esc, timeout),
   activity viewer, demo launcher.
3. **Isolation spike**: run `xor-demo-reader` under bubblewrap with the
   broker socket as its only file access; measure what breaks — this
   de-risks Milestones 3–4 and hardens the identity story.
