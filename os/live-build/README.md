# OS image build (Debian Live Build)

Produces the bootable XOR OS x86-64 live ISO: Debian 13 (trixie) +
XFCE + XOR broker/desktop components.

```sh
sudo tools/build-iso.sh     # from the repo root; needs live-build + root
tools/run-qemu.sh           # boot the result in the fixed QEMU target
```

## How it fits together

| Piece | Role |
|---|---|
| `auto/config` | `lb config` parameters (Debian trixie, amd64, syslinux+grub-efi, live user `user`) |
| `config/package-lists/xor.list.chroot` | XFCE, LightDM, Python/GTK runtime, theme packages, tools |
| `config/includes.chroot/etc/lightdm/...` | Autologin live session for unattended QEMU boots |
| `config/includes.chroot/opt/xor-dist/` | **Staged at build time** by `tools/build-iso.sh` from `broker/`, `desktop/`, `assistant/` (git-ignored; do not edit by hand) |
| `config/hooks/normal/0900-xor-install.hook.chroot` | Installs XOR commands, menu entries, autostart, wallpaper and XFCE skel defaults; self-checks imports |

Status: **not yet built in the cloud dev sandbox** (no live-build/Debian
mirror/privileges there). See `docs/TEST_RESULTS.md` for what is
verified and `docs/DEVELOPMENT.md` for exact local build steps and
troubleshooting. Nothing in this directory is claimed boot-verified
until a team machine produces and boots an ISO.
