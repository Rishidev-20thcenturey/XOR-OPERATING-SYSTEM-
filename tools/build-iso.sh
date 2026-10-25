#!/bin/sh
# Build the XOR OS live ISO with Debian Live Build.
#
# Requirements (checked up front, script stops on any failure):
#   * Debian/Ubuntu host with: live-build, debootstrap tooling
#   * root (lb build needs chroot privileges): run with sudo
#   * network access to a Debian mirror, ~10 GB free disk
# Usage: sudo tools/build-iso.sh [--clean]
# Output: os/live-build/*.iso + .sha256 + BUILD-MANIFEST.txt
set -eu

REPO=$(cd "$(dirname "$0")/.." && pwd)
LBDIR="$REPO/os/live-build"
STAGE="$LBDIR/config/includes.chroot/opt/xor-dist"

fail() { echo "ERROR: $*" >&2; exit 1; }
info() { printf '\n== %s ==\n' "$*"; }

info "Checking prerequisites"
command -v lb >/dev/null 2>&1 || fail "Debian Live Build ('lb') not found.
  Install on Debian/Ubuntu:  sudo apt install live-build
  (Building the ISO is a local-machine task; cloud sandboxes without
   privileges/mirror access cannot run it - see docs/TEST_RESULTS.md.)"
[ "$(id -u)" = "0" ] || fail "lb build needs root. Re-run: sudo $0"
command -v python3 >/dev/null 2>&1 || fail "python3 not found"
df_avail=$(df --output=avail -BG "$REPO" | tail -1 | tr -dc '0-9')
[ "${df_avail:-0}" -ge 10 ] || echo "WARNING: less than 10G free disk; build may run out of space" >&2

if [ "${1:-}" = "--clean" ]; then
    info "Cleaning previous build"
    (cd "$LBDIR" && lb clean || true)
    rm -rf "$STAGE" "$LBDIR"/cache "$LBDIR"/*.iso "$LBDIR"/*.sha256 "$LBDIR"/BUILD-MANIFEST.txt
fi

info "Staging XOR sources into the image tree"
rm -rf "$STAGE"
mkdir -p "$STAGE"
cp -r "$REPO/broker" "$REPO/desktop" "$REPO/assistant" "$STAGE/"
find "$STAGE" -type d -name __pycache__ -prune -exec rm -rf {} +
find "$STAGE" -name '*.egg-info' -exec rm -rf {} + 2>/dev/null || true
echo "Staged: $(find "$STAGE" -type f | wc -l) files"

info "Configuring (lb config)"
cd "$LBDIR"
sh auto/config

info "Building (lb build) - this downloads Debian packages, be patient"
lb build 2>&1 | tee build.log

ISO=$(ls -1 ./*.iso 2>/dev/null | head -n1 || true)
[ -n "$ISO" ] || fail "Build finished but no ISO was produced; see $LBDIR/build.log"

info "Checksumming and writing build manifest"
sha256sum "$(basename "$ISO")" > "$(basename "$ISO").sha256"
GIT_REV=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || echo "unknown")
GIT_DIRTY=$(git -C "$REPO" status --porcelain 2>/dev/null | wc -l)
{
    echo "XOR OS build manifest"
    echo "iso:            $(basename "$ISO")"
    echo "sha256:         $(cut -d' ' -f1 "$(basename "$ISO").sha256")"
    echo "size_bytes:     $(stat -c%s "$ISO")"
    echo "base:           Debian trixie (13) amd64, XFCE, live-build"
    echo "live-build:     $(lb --version 2>&1 | head -n1)"
    echo "built_utc:      $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "git_revision:   $GIT_REV (uncommitted changes: $GIT_DIRTY files)"
    echo "packages:       see config/package-lists/xor.list.chroot"
    echo "package_list_sha256: $(sha256sum config/package-lists/xor.list.chroot | cut -d' ' -f1)"
    echo "reproducibility: NOT bit-for-bit claimed (mirror timestamps/content vary)"
} > BUILD-MANIFEST.txt

info "Done"
cat BUILD-MANIFEST.txt
echo
echo "Boot it with:  tools/run-qemu.sh $ISO"
