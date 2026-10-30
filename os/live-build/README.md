# OS image build (Debian Live Build)

Produces the bootable XOR OS x86-64 live ISO: Debian 13 (trixie) +
XFCE fallback session + XOR broker and Aurora Shell desktop components.

```sh
sudo tools/build-iso.sh     # from the repo root; needs live-build + root
tools/run-qemu.sh           # boot the result in the fixed QEMU target
```

Before `lb build`, the script validates every package name and dependency
closure against the Debian trixie, trixie-updates and trixie-security
indices (`python3 tools/check-packages.py`). It also preserves `lb`'s exit
status through the `tee` log pipeline and snapshots existing ISOs before
the build; only an ISO that is new or changed is accepted, so a failed
build cannot report an old ISO as the current artifact. Existing
artifacts are not deleted; only `--clean` removes prior build outputs.

## How it fits together

| Piece | Role |
|---|---|
| `auto/config` | `lb config` parameters (Debian trixie, amd64, syslinux+grub-efi, live user `user`) |
| `config/package-lists/xor.list.chroot` | XFCE, LightDM, Python/GTK + Wnck runtime, theme packages, tools |
| `config/includes.chroot/etc/lightdm/...` | Autologin live session for unattended QEMU boots |
| `config/includes.chroot/opt/xor-dist/` | **Staged at build time** by `tools/build-iso.sh` from `broker/`, `desktop/`, `assistant/` (git-ignored; do not edit by hand) |
| `config/hooks/normal/0900-xor-install.hook.chroot` | Installs XOR broker/shell/control commands, menu entries, broker + Aurora autostart, wallpaper set/logo and XFCE skel defaults; self-checks imports and headless shell status paths |

Status: the ISO build passed in GitHub Actions run 40819648638 (commit
`2ac4234f19cccf680ed75deabaa3feffdc0677e9`) using Debian Live Build
`20250505+deb13u1`; the ISO is 1,858,633,728 bytes with SHA-256
`08af43a2a6d1bd3207c627b7b47d11344e5d347c6a746c1e60a3a0e1675dfa83` and
is artifact-backed. The direct-kernel QEMU/TCG check passed, the ISO also
booted through OVMF/UEFI firmware (no direct kernel/initrd) to the XOR
desktop with a non-blank screenshot, and the same run installed the ISO
to a 20 GiB virtio disk and booted that disk again (`result=PASS`,
`install_result=PASS`, `second_boot_result=PASS`). A follow-up run
(40997749602) booted the same ISO through QEMU SeaBIOS. The direct QEMU
check extracts the kernel/initrd and the install check boots the
installer kernel directly; the UEFI and BIOS checks are the
firmware/bootloader paths. Secure Boot and firmware menus are still not
exercised. See `docs/TEST_RESULTS.md` for exact build, artifact, QEMU,
UEFI, BIOS and install evidence, and `docs/DEVELOPMENT.md` for local
build steps and troubleshooting.
