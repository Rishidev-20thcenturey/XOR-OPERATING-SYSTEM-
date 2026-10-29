#!/bin/sh
# Boot the built XOR ISO through OVMF/UEFI, wait for the live graphical
# target, and capture a screenshot.  This is the firmware gate that the
# direct-kernel QEMU check does not cover.
#
# Usage: tools/test-uefi-qemu.sh ISO [EVIDENCE_DIR]
# Exit status is 0 after writing evidence; result.txt says PASS/FAIL.
set -eu

ISO=${1:?usage: test-uefi-qemu.sh ISO [EVIDENCE_DIR]}
OUT=${2:-uefi-evidence}
TIMEOUT=${UEFI_TIMEOUT:-1800}
SCREENSHOT_WAIT=${UEFI_SCREENSHOT_WAIT:-240}

fail() {
    echo "ERROR: $*" >&2
    exit 2
}

command -v qemu-system-x86_64 >/dev/null 2>&1 || fail "qemu-system-x86_64 not installed"
command -v socat >/dev/null 2>&1 || fail "socat not installed"
[ -f "$ISO" ] || fail "ISO not found: $ISO"

rm -rf "$OUT"
mkdir -p "$OUT"
ISO=$(cd "$(dirname "$ISO")" && pwd)/$(basename "$ISO")
SERIAL="$OUT/serial.log"
MONITOR="$OUT/monitor.sock"
PPM="$OUT/screenshot.ppm"
PNG="$OUT/screenshot.png"
RESULT=FAIL
GRAPHICAL=0
DISPLAY_START=0
SETTLED=0
SIGNAL=""
QEMU_RC="not-started"
SCREENSHOT="not-captured"
SCREENSHOT_OK=0
QEMU_PID=
FIRMWARE_MODE=""
FIRMWARE_CODE=""
FIRMWARE_VARS=""
FW_ARGS=""

# shellcheck disable=SC2329  # invoked through trap
cleanup() {
    if [ -n "$QEMU_PID" ] && kill -0 "$QEMU_PID" 2>/dev/null; then
        kill "$QEMU_PID" 2>/dev/null || true
        wait "$QEMU_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT HUP INT TERM

snapshot() {
    name=$1
    [ -S "$MONITOR" ] || return 0
    snap_ppm="$OUT/$name.ppm"
    snap_png="$OUT/$name.png"
    printf 'screendump %s\n' "$snap_ppm" | \
        timeout 10 socat - UNIX-CONNECT:"$MONITOR" >/dev/null 2>&1 || return 0
    [ -s "$snap_ppm" ] || return 0
    if command -v convert >/dev/null 2>&1; then
        convert "$snap_ppm" "$snap_png" >/dev/null 2>&1 || true
    fi
    return 0
}

press_enter() {
    [ -S "$MONITOR" ] || return 0
    printf 'sendkey ret\n' | \
        timeout 10 socat - UNIX-CONNECT:"$MONITOR" >/dev/null 2>&1 || true
}

capture_screenshot() {
    [ -S "$MONITOR" ] || return 1
    command -v convert >/dev/null 2>&1 || return 1
    rm -f "$PPM" "$PNG"
    if ! timeout 15 sh -c "printf 'screendump %s\\n' '$PPM' | socat - UNIX-CONNECT:'$MONITOR'" \
        > "$OUT/screendump.log" 2>&1; then
        return 1
    fi
    [ -s "$PPM" ] || return 1
    convert "$PPM" "$PNG" >> "$OUT/screendump.log" 2>&1 || return 1
    if [ -s "$PNG" ] && \
        [ "$(stat -c%s "$PNG" 2>/dev/null || echo 0)" -ge 10000 ]; then
        SCREENSHOT=$PNG
        return 0
    fi
    return 1
}

finish() {
    {
        echo "result=$RESULT"
        echo "graphical_signal=$GRAPHICAL"
        echo "signal=$SIGNAL"
        echo "qemu_exit=$QEMU_RC"
        echo "serial_log=$SERIAL"
        echo "screenshot=$SCREENSHOT"
        echo "screenshot_ok=$SCREENSHOT_OK"
        echo "firmware_mode=$FIRMWARE_MODE"
        echo "firmware_code=$FIRMWARE_CODE"
        echo "firmware_vars=$FIRMWARE_VARS"
        echo "iso=$ISO"
        echo "iso_sha256=$(sha256sum "$ISO" | cut -d' ' -f1)"
        echo "note=ISO booted through OVMF/UEFI; no direct kernel/initrd was supplied."
    } > "$OUT/result.txt"
    cat "$OUT/result.txt"
    exit 0
}

# Prefer a matching code/vars pair; fall back to a combined firmware image.
if [ -f /usr/share/OVMF/OVMF_CODE_4M.fd ] && [ -f /usr/share/OVMF/OVMF_VARS_4M.fd ]; then
    FIRMWARE_MODE=pflash
    FIRMWARE_CODE=/usr/share/OVMF/OVMF_CODE_4M.fd
    FIRMWARE_VARS="$OUT/OVMF_VARS.fd"
    cp /usr/share/OVMF/OVMF_VARS_4M.fd "$FIRMWARE_VARS"
elif [ -f /usr/share/OVMF/OVMF_CODE.fd ] && [ -f /usr/share/OVMF/OVMF_VARS.fd ]; then
    FIRMWARE_MODE=pflash
    FIRMWARE_CODE=/usr/share/OVMF/OVMF_CODE.fd
    FIRMWARE_VARS="$OUT/OVMF_VARS.fd"
    cp /usr/share/OVMF/OVMF_VARS.fd "$FIRMWARE_VARS"
elif [ -f /usr/share/qemu/OVMF.fd ]; then
    FIRMWARE_MODE=bios
    FIRMWARE_CODE=/usr/share/qemu/OVMF.fd
elif [ -f /usr/share/OVMF/OVMF.fd ]; then
    FIRMWARE_MODE=bios
    FIRMWARE_CODE=/usr/share/OVMF/OVMF.fd
else
    RESULT=FAIL
    SIGNAL="OVMF firmware not found"
    finish
fi

if [ "$FIRMWARE_MODE" = pflash ]; then
    FW_ARGS="-drive if=pflash,format=raw,readonly=on,file=$FIRMWARE_CODE -drive if=pflash,format=raw,file=$FIRMWARE_VARS"
else
    FW_ARGS="-bios $FIRMWARE_CODE"
fi

echo "Booting $ISO through $FIRMWARE_MODE firmware (timeout ${TIMEOUT}s)"
# shellcheck disable=SC2086  # firmware arguments are a controlled word list
qemu-system-x86_64 \
    -machine q35,accel=tcg \
    -cpu qemu64 \
    -m 4096 -smp 2 \
    $FW_ARGS \
    -drive file="$ISO",media=cdrom,readonly=on,if=none,id=cdrom0 \
    -device ide-cd,drive=cdrom0 \
    -display none \
    -vga none \
    -device virtio-vga \
    -device virtio-net-pci,netdev=net0 \
    -netdev user,id=net0 \
    -vnc 127.0.0.1:2 \
    -serial "file:$SERIAL" \
    -monitor "unix:$MONITOR,server,nowait" \
    -boot order=d \
    -no-reboot \
    > "$OUT/qemu-stdout.log" 2>&1 &
QEMU_PID=$!
echo "$QEMU_PID" > "$OUT/qemu.pid"

# Capture the firmware/GRUB handoff even when Linux never reaches the desktop.
sleep 15
snapshot boot-015s
press_enter
sleep 30
snapshot boot-045s
press_enter
sleep 60
snapshot boot-105s

for _ in $(seq 1 $((TIMEOUT / 10))); do
    if [ -f "$SERIAL" ]; then
        if grep -Eqi \
            'Reached target Graphical Interface|Started LightDM|Started lightdm\.service' \
            "$SERIAL"; then
            GRAPHICAL=1
            SIGNAL=$(grep -Ei 'Reached target Graphical Interface|Started LightDM|Started lightdm\.service' "$SERIAL" | tail -n1)
            break
        fi
        # Match the word, not the full line: systemd inserts ANSI escapes
        # between the message and the unit name on the serial console.
        if grep -Eqi 'lightdm' "$SERIAL"; then
            DISPLAY_START=1
        fi
    fi

    # LightDM switches the active VT, so the graphical-target line may never
    # reach the serial console.  After LightDM starts, wait for XFCE to paint
    # and use a non-blank framebuffer as the real proof.
    if [ "$DISPLAY_START" = 1 ] && [ "$SETTLED" = 0 ]; then
        sleep 60
        SETTLED=1
    fi
    if [ "$SETTLED" = 1 ] && capture_screenshot; then
        GRAPHICAL=1
        SCREENSHOT_OK=1
        SIGNAL="non-blank framebuffer after LightDM started"
        break
    fi

    if ! kill -0 "$QEMU_PID" 2>/dev/null; then
        QEMU_RC=0
        wait "$QEMU_PID" || QEMU_RC=$?
        break
    fi
    sleep 10
done

if [ "$GRAPHICAL" = 1 ] && [ "$SCREENSHOT_OK" != 1 ]; then
    sleep "$SCREENSHOT_WAIT"
    for attempt in 1 2 3; do
        if capture_screenshot; then
            SCREENSHOT_OK=1
            break
        fi
        echo "screenshot attempt $attempt was blank or too small; retrying"
        sleep 60
    done
fi

if [ "$GRAPHICAL" = 1 ] && [ "$SCREENSHOT_OK" = 1 ]; then
    RESULT=PASS
elif [ "$GRAPHICAL" = 1 ]; then
    RESULT=FAIL
    SIGNAL="graphical target reached but screenshot was blank or too small"
elif [ "$QEMU_RC" != "not-started" ]; then
    RESULT=FAIL
    SIGNAL="QEMU exited before a graphical signal (exit $QEMU_RC)"
else
    RESULT=FAIL
    SIGNAL="no graphical signal within the timeout"
fi

if [ -f "$SCREENSHOT" ] && command -v identify >/dev/null 2>&1; then
    identify "$SCREENSHOT" > "$OUT/screenshot-info.txt" 2>&1 || true
fi

if kill -0 "$QEMU_PID" 2>/dev/null; then
    if [ -S "$MONITOR" ]; then
        printf 'quit\n' | timeout 5 socat - UNIX-CONNECT:"$MONITOR" > /dev/null 2>&1 || true
    fi
    sleep 5
    kill "$QEMU_PID" 2>/dev/null || true
fi
wait "$QEMU_PID" 2>/dev/null || true
finish
