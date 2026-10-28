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

Status: the ISO build passed in GitHub Actions run 40597398794 (commit
`26e87fdddc8016ece8cadb0d24d1d971d1ba9eb3`) using Debian Live Build
`20250505+deb13u1`; the ISO is 1,464,860,672 bytes with SHA-256
`2fb52d345a9209fae87a32965f32d64e5fcb75ce2ad207dd895d79034ed80603` and
is artifact-backed. The bounded QEMU/TCG check reached a live XFCE session
and captured a screenshot showing the Aurora panel/dock and Royal Aurora
wallpaper. The check extracts the kernel/initrd and attaches the ISO as a
CD-ROM, so firmware/bootloader selection is not exercised. See
`docs/TEST_RESULTS.md` for exact build, artifact and QEMU evidence, and
`docs/DEVELOPMENT.md` for local build steps and troubleshooting.
