#!/bin/sh
# Regression tests for tools/build-iso.sh artifact handling.
#
# Uses a fake lb/id/df and a tiny temporary repository, so it needs no root,
# no Debian host and no network.  It proves that:
#   * a failed "lb build" is not hidden by the tee pipeline;
#   * a stale ISO is never selected as the current build's artifact;
#   * an old ISO is preserved when a build fails (no silent deletion).
set -eu

REPO=$(cd "$(dirname "$0")/.." && pwd)
TMP=$(mktemp -d "${TMPDIR:-/tmp}/xor-build-test.XXXXXX")
trap 'rm -rf "$TMP"' EXIT HUP INT TERM
FAKEBIN="$TMP/bin"
mkdir -p "$FAKEBIN"

cat > "$FAKEBIN/id" <<'EOF'
#!/bin/sh
echo 0
EOF
cat > "$FAKEBIN/df" <<'EOF'
#!/bin/sh
echo "20G"
EOF
cat > "$FAKEBIN/lb" <<'EOF'
#!/bin/sh
case "${1:-}" in
    config|clean) exit 0 ;;
    --version) echo "live-build fake 1.0" ;;
    build)
        echo "fake lb build output"
        if [ "${FAKE_LB_FAIL:-0}" = "1" ]; then
            exit 7
        fi
        if [ "${FAKE_LB_CREATE:-1}" = "1" ]; then
            printf 'fake ISO\n' > live-image-amd64.hybrid.iso
        fi
        exit 0
        ;;
    *) exit 0 ;;
esac
EOF
chmod +x "$FAKEBIN/id" "$FAKEBIN/df" "$FAKEBIN/lb"

prepare_repo() {
    rm -rf "$TMP/repo"
    mkdir -p "$TMP/repo/tools" \
        "$TMP/repo/os/live-build/auto" \
        "$TMP/repo/os/live-build/config/package-lists" \
        "$TMP/repo/broker" "$TMP/repo/desktop" "$TMP/repo/assistant"
    cp "$REPO/tools/build-iso.sh" "$TMP/repo/tools/build-iso.sh"
    cp "$REPO/os/live-build/auto/config" "$TMP/repo/os/live-build/auto/config"
    cp "$REPO/os/live-build/config/package-lists/xor.list.chroot" \
        "$TMP/repo/os/live-build/config/package-lists/xor.list.chroot"
    printf 'import os, sys\nsys.exit(int(os.environ.get("FAKE_CHECK_RC", "0")))\n' \
        > "$TMP/repo/tools/check-packages.py"
    printf 'stub\n' > "$TMP/repo/broker/stub"
    printf 'stub\n' > "$TMP/repo/desktop/stub"
    printf 'stub\n' > "$TMP/repo/assistant/stub"
    printf 'stale ISO from an earlier build\n' \
        > "$TMP/repo/os/live-build/aaa-stale.iso"
    # Future-dated stale file: a plain "mtime newer than build start" check
    # would mistake this for a fresh artifact.  The snapshot must not.
    touch -t 203001010000 "$TMP/repo/os/live-build/aaa-stale.iso"
}

run_case() {
    CASE=$1
    CASE_FAIL=$2
    CASE_CREATE=$3
    CASE_CHECK=$4
    prepare_repo
    set +e
    PATH="$FAKEBIN:$PATH" \
        FAKE_LB_FAIL="$CASE_FAIL" \
        FAKE_LB_CREATE="$CASE_CREATE" \
        FAKE_CHECK_RC="$CASE_CHECK" \
        sh "$TMP/repo/tools/build-iso.sh" > "$TMP/$CASE.log" 2>&1
    CASE_RC=$?
    set -e
    printf '  %s: exit %s\n' "$CASE" "$CASE_RC"
}

fail_case() {
    printf 'FAIL: %s\n' "$*" >&2
    cat "$TMP/$CASE.log" >&2
    exit 1
}

printf 'Build script artifact tests\n'
run_case failed_build 1 0 0
[ "$CASE_RC" -ne 0 ] || fail_case "failed lb build returned success"
grep -q "lb build failed" "$TMP/failed_build.log" \
    || fail_case "failure message did not mention lb build"
[ -f "$TMP/repo/os/live-build/aaa-stale.iso" ] \
    || fail_case "failed build deleted the old ISO"
[ ! -f "$TMP/repo/os/live-build/live-image-amd64.hybrid.iso" ] \
    || fail_case "failed build produced a new ISO"

run_case bad_packages 0 1 1
[ "$CASE_RC" -ne 0 ] || fail_case "failed package validation returned success"
grep -q "Package list validation failed" "$TMP/bad_packages.log" \
    || fail_case "package validation failure was not reported"
[ -f "$TMP/repo/os/live-build/aaa-stale.iso" ] \
    || fail_case "package validation failure deleted the old ISO"
[ ! -f "$TMP/repo/os/live-build/live-image-amd64.hybrid.iso" ] \
    || fail_case "package validation failure still ran lb build"

run_case fresh_artifact 0 1 0
[ "$CASE_RC" -eq 0 ] || fail_case "successful fake build returned failure"
[ -f "$TMP/repo/os/live-build/live-image-amd64.hybrid.iso" ] \
    || fail_case "successful build did not produce its ISO"
[ -f "$TMP/repo/os/live-build/aaa-stale.iso" ] \
    || fail_case "successful build deleted the old ISO"
grep -q '^iso: *live-image-amd64.hybrid.iso$' \
    "$TMP/repo/os/live-build/BUILD-MANIFEST.txt" \
    || fail_case "manifest did not name the fresh ISO"
if grep -q 'aaa-stale.iso' "$TMP/repo/os/live-build/BUILD-MANIFEST.txt"; then
    fail_case "manifest named the stale ISO"
fi

run_case missing_artifact 0 0 0
[ "$CASE_RC" -ne 0 ] || fail_case "success with no new ISO returned success"
grep -q "refusing to reuse a stale ISO" "$TMP/missing_artifact.log" \
    || fail_case "missing-artifact failure did not explain the stale ISO guard"
[ -f "$TMP/repo/os/live-build/aaa-stale.iso" ] \
    || fail_case "missing-artifact build deleted the old ISO"

printf 'Build script artifact tests passed\n'
