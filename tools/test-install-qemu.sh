#!/bin/sh
# Install the built XOR ISO onto a disposable virtio disk, then boot that
# disk a second time.  This is the install-to-disk gate for CI; it needs
# qemu-system-x86_64, qemu-img, xorriso and the Debian installer included by
# --debian-installer live.
#
# Usage: tools/test-install-qemu.sh ISO [EVIDENCE_DIR]
# Exit status is 0 after writing evidence; result.txt says PASS/FAIL.
set -eu

ISO=${1:?usage: test-install-qemu.sh ISO [EVIDENCE_DIR]}
OUT=${2:-install-evidence}
INSTALL_TIMEOUT=${INSTALL_TIMEOUT:-2700}
SECOND_BOOT_TIMEOUT=${SECOND_BOOT_TIMEOUT:-900}
DISK_SIZE=${DISK_SIZE:-20G}

fail() {
    echo "ERROR: $*" >&2
    exit 2
}

command -v qemu-system-x86_64 >/dev/null 2>&1 || fail "qemu-system-x86_64 not installed"
command -v qemu-img >/dev/null 2>&1 || fail "qemu-img not installed"
command -v xorriso >/dev/null 2>&1 || fail "xorriso not installed"
[ -f "$ISO" ] || fail "ISO not found: $ISO"

rm -rf "$OUT"
mkdir -p "$OUT"
ISO=$(cd "$(dirname "$ISO")" && pwd)/$(basename "$ISO")
INSTALL_SERIAL="$OUT/install-serial.log"
SECOND_SERIAL="$OUT/second-boot-serial.log"
RESULT=FAIL
INSTALL_RESULT=NOT_RUN
SECOND_RESULT=NOT_RUN
QEMU_PID=
SECOND_PID=

# shellcheck disable=SC2329  # invoked through trap
cleanup() {
    if [ -n "$QEMU_PID" ] && kill -0 "$QEMU_PID" 2>/dev/null; then
        kill "$QEMU_PID" 2>/dev/null || true
        wait "$QEMU_PID" 2>/dev/null || true
    fi
    if [ -n "$SECOND_PID" ] && kill -0 "$SECOND_PID" 2>/dev/null; then
        kill "$SECOND_PID" 2>/dev/null || true
        wait "$SECOND_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT HUP INT TERM

finish() {
    {
        echo "result=$RESULT"
        echo "install_result=$INSTALL_RESULT"
        echo "second_boot_result=$SECOND_RESULT"
        echo "iso=$ISO"
        echo "iso_sha256=$(sha256sum "$ISO" | cut -d' ' -f1)"
        echo "disk=$OUT/disk.qcow2"
        echo "install_serial=$INSTALL_SERIAL"
        echo "second_boot_serial=$SECOND_SERIAL"
        echo "note=The installer kernel/initrd were booted directly from the ISO; GRUB/firmware selection is not part of this install gate. The second boot used the installed disk only."
    } > "$OUT/result.txt"
    cat "$OUT/result.txt"
    exit 0
}

wait_for() {
    pattern=$1
    file=$2
    timeout=$3
    pid=$4
    elapsed=0
    while [ "$elapsed" -lt "$timeout" ]; do
        if [ -f "$file" ] && grep -Eqi "$pattern" "$file"; then
            return 0
        fi
        if [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; then
            [ -f "$file" ] && grep -Eqi "$pattern" "$file" && return 0
            return 1
        fi
        sleep 5
        elapsed=$((elapsed + 5))
    done
    return 1
}

echo "Extracting installer from $ISO"
if ! xorriso -osirrox on -indev "$ISO" -extract /install "$OUT/install" \
        > "$OUT/xorriso-install.log" 2>&1; then
    INSTALL_RESULT=FAIL
    echo "could not extract /install from ISO" >&2
    finish
fi
KERNEL=$(find -L "$OUT/install" -maxdepth 2 -type f -name 'vmlinuz*' | head -n1)
INITRD=$(find -L "$OUT/install" -maxdepth 2 -type f -name 'initrd*' | head -n1)
[ -n "$KERNEL" ] && [ -n "$INITRD" ] || {
    INSTALL_RESULT=FAIL
    echo "installer kernel/initrd not found" >&2
    finish
}
echo "installer_kernel=$KERNEL" > "$OUT/install-inputs.txt"
echo "installer_initrd=$INITRD" >> "$OUT/install-inputs.txt"

qemu-img create -f qcow2 "$OUT/disk.qcow2" "$DISK_SIZE" >/dev/null
qemu-img info "$OUT/disk.qcow2" > "$OUT/disk-before.txt"

echo "Booting installer (timeout ${INSTALL_TIMEOUT}s)"
qemu-system-x86_64 \
    -machine q35,accel=tcg \
    -cpu qemu64 \
    -m 4096 -smp 2 \
    -kernel "$KERNEL" \
    -initrd "$INITRD" \
    -append "auto=true priority=critical console=ttyS0,115200n8 preseed/file=/cdrom/install/preseed.cfg" \
    -drive file="$OUT/disk.qcow2",if=virtio,format=qcow2 \
    -cdrom "$ISO" \
    -netdev user,id=net0 \
    -device virtio-net-pci,netdev=net0 \
    -display none \
    -vga none \
    -serial "file:$INSTALL_SERIAL" \
    -no-reboot \
    > "$OUT/install-stdout.log" 2>&1 &
QEMU_PID=$!

if wait_for 'Installation complete|Reboot: Power down|reboot: Restarting system|Power down' \
        "$INSTALL_SERIAL" "$INSTALL_TIMEOUT" "$QEMU_PID"; then
    INSTALL_RESULT=PASS
else
    INSTALL_RESULT=FAIL
fi
if kill -0 "$QEMU_PID" 2>/dev/null; then
    kill "$QEMU_PID" 2>/dev/null || true
fi
wait "$QEMU_PID" 2>/dev/null || true
QEMU_PID=
qemu-img info "$OUT/disk.qcow2" > "$OUT/disk-after-install.txt"

if [ "$INSTALL_RESULT" != PASS ]; then
    echo "installer did not report completion" >&2
    finish
fi

echo "Booting installed disk (timeout ${SECOND_BOOT_TIMEOUT}s)"
qemu-system-x86_64 \
    -machine q35,accel=tcg \
    -cpu qemu64 \
    -m 4096 -smp 2 \
    -boot order=c \
    -drive file="$OUT/disk.qcow2",if=virtio,format=qcow2 \
    -netdev user,id=net0 \
    -device virtio-net-pci,netdev=net0 \
    -display none \
    -device virtio-vga \
    -serial "file:$SECOND_SERIAL" \
    -no-reboot \
    > "$OUT/second-boot-stdout.log" 2>&1 &
SECOND_PID=$!

if wait_for 'Reached target Graphical Interface|Started LightDM|Started lightdm\.service|XOR login:|login:' \
        "$SECOND_SERIAL" "$SECOND_BOOT_TIMEOUT" "$SECOND_PID"; then
    SECOND_RESULT=PASS
else
    SECOND_RESULT=FAIL
fi
if kill -0 "$SECOND_PID" 2>/dev/null; then
    kill "$SECOND_PID" 2>/dev/null || true
fi
wait "$SECOND_PID" 2>/dev/null || true
SECOND_PID=

if [ "$INSTALL_RESULT" = PASS ] && [ "$SECOND_RESULT" = PASS ]; then
    RESULT=PASS
fi
finish
