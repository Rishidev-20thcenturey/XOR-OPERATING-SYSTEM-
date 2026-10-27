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
    (
        cd "$LBDIR" || exit 0
        lb clean || true
    )
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

info "Validating package list against Debian trixie"
python3 "$REPO/tools/check-packages.py" \
    --package-list "$LBDIR/config/package-lists/xor.list.chroot" \
    || fail "Package list validation failed; fix the list before building."

# The status-file dance preserves lb's exit status through tee under POSIX
# sh (dash has no pipefail).  The pre-build snapshot prevents an old ISO
# from being reported as this build's artifact, even if the old file has a
# future mtime or the build silently produces nothing.
ISO_SNAPSHOT="$LBDIR/.iso-snapshot.$$"
LB_STATUS="$LBDIR/.lb-status.$$"
cleanup_build_files() {
    rm -f "$ISO_SNAPSHOT" "$LB_STATUS"
}
trap cleanup_build_files EXIT HUP INT TERM
: > "$ISO_SNAPSHOT" || fail "Cannot create build snapshot: $ISO_SNAPSHOT"
for existing_iso in "$LBDIR"/*.iso; do
    [ -f "$existing_iso" ] || continue
    stat -c '%n %d %i %s %Y' "$existing_iso" >> "$ISO_SNAPSHOT" \
        || fail "Cannot snapshot existing ISO: $existing_iso"
done

info "Building (lb build) - this downloads Debian packages, be patient"
{ set +e; lb build 2>&1; echo $? > "$LB_STATUS"; } | tee build.log
LB_RC=$(cat "$LB_STATUS" 2>/dev/null || echo 1)
[ "$LB_RC" -eq 0 ] || fail "lb build failed (exit $LB_RC); see $LBDIR/build.log"

ISO=""
for candidate in "$LBDIR"/*.iso; do
    [ -f "$candidate" ] || continue
    candidate_sig=$(stat -c '%d %i %s %Y' "$candidate") \
        || fail "Cannot stat ISO candidate: $candidate"
    if ! grep -Fqx "$candidate $candidate_sig" "$ISO_SNAPSHOT"; then
        ISO="$candidate"
        break
    fi
done
[ -n "$ISO" ] || fail "lb build exited 0 but no ISO changed or appeared during this build; refusing to reuse a stale ISO"
cleanup_build_files
trap - EXIT HUP INT TERM

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
