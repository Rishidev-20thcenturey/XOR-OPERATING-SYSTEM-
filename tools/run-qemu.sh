#!/bin/sh
# Boot XOR OS in the fixed virtual hardware target (Milestone 0).
#
# Virtual machine definition (x86-64):
#   machine q35 · 2 vCPU · 4 GiB RAM · KVM if available else TCG
#   display: virtio-VGA            storage: CD-ROM (IDE) + virtio-blk qcow2 disk
#   network: virtio-net (user NAT) input:   USB keyboard + USB tablet (xHCI)
#   debug:   serial console -> xor-serial.log, QEMU monitor on Ctrl+Alt+2
# Firmware: UEFI (OVMF) if found, else SeaBIOS (ISO is hybrid; both boot).
#
# Usage:
#   tools/run-qemu.sh [disk-or-ISO] [--headless] [--fresh]
#     no args        boot newest os/live-build/*.iso with xor-QEMU-disk.qcow2
#     --headless     serial console in terminal, no window (debugging)
#     --fresh        delete and recreate the qcow2 disk first
set -eu

REPO=$(cd "$(dirname "$0")/.." && pwd)
DISK="$REPO/xor-QEMU-disk.qcow2"
SERIAL_LOG="$REPO/xor-serial.log"
ISO=""
HEADLESS=0
FRESH=0

for a in "$@"; do
    case "$a" in
        --headless) HEADLESS=1 ;;
        --fresh) FRESH=1 ;;
        -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) ISO="$a" ;;
    esac
done

command -v qemu-system-x86_64 >/dev/null 2>&1 || {
    echo "ERROR: qemu-system-x86_64 not found." >&2
    echo "  Install on Debian/Ubuntu: sudo apt install qemu-system-x86 ovmf" >&2
    echo "  (No /dev/KVM? The VM still runs, just slower under TCG emulation.)" >&2
    exit 1
}

if [ -z "$ISO" ]; then
    ISO=$(ls -t "$REPO"/os/live-build/*.iso 2>/dev/null | head -n1 || true)
    [ -n "$ISO" ] || { echo "ERROR: no ISO found in os/live-build/. Build one: sudo tools/build-iso.sh" >&2; exit 1; }
fi
[ -f "$ISO" ] || { echo "ERROR: '$ISO' not found" >&2; exit 1; }

if [ "$FRESH" = 1 ] && [ -f "$DISK" ]; then
    echo "Removing existing virtual disk $DISK"
    rm -f "$DISK"
fi
if [ ! -f "$DISK" ]; then
    command -v qemu-img >/dev/null 2>&1 || { echo "ERROR: qemu-img not found (package: qemu-utils)" >&2; exit 1; }
    echo "Creating 16G virtual disk (qcow2, grows as used): $DISK"
    qemu-img create -f qcow2 "$DISK" 16G >/dev/null
fi

ACCEL="tcg"; CPU="qemu64"
if [ -w /dev/KVM ]; then
    ACCEL="KVM"; CPU="host"
else
    echo "NOTE: /dev/KVM unavailable - using TCG emulation (slower boot)."
fi

FIRMWARE_ARGS=""
for ovmf in /usr/share/OVMF/OVMF_CODE_4M.fd /usr/share/OVMF/OVMF_CODE.fd /usr/share/qemu/OVMF.fd; do
    if [ -f "$ovmf" ]; then
        FIRMWARE_ARGS="-drive if=pflash,format=raw,readonly=on,file=$ovmf"
        echo "Firmware: UEFI ($ovmf)"
        break
    fi
done
[ -n "$FIRMWARE_ARGS" ] || echo "Firmware: SeaBIOS (OVMF not found; hybrid ISO still boots)"

if [ "$HEADLESS" = 1 ]; then
    DISPLAY_ARGS="-nographic"
    SERIAL_ARGS="-serial mon:stdio"
    echo "Headless mode: serial console here; add 'console=ttyS0' at the boot prompt."
    echo "Exit QEMU with Ctrl+A then X."
else
    DISPLAY_ARGS="-display gtk,window-close=on"
    SERIAL_ARGS="-serial file:$SERIAL_LOG"
    echo "Serial kernel log: $SERIAL_LOG (boot with 'console=ttyS0' for output)"
fi

echo "Booting $ISO"
echo "Live user: 'user' (password 'live' if prompted; autologin is enabled)."
# shellcheck disable=SC2086
exec qemu-system-x86_64 \
    -machine "q35,accel=$ACCEL" \
    -cpu "$CPU" \
    -m 4096 \
    -smp 2 \
    $FIRMWARE_ARGS \
    -drive file="$ISO",media=cdrom,if=ide,index=2,readonly=on \
    -drive file="$DISK",if=none,id=disk0,format=qcow2 \
    -device virtio-blk-pci,drive=disk0 \
    -device virtio-VGA \
    -device virtio-net-pci,netdev=net0 \
    -netdev user,id=net0 \
    -device qemu-xhci,id=usb0 \
    -device usb-KVM,bus=usb0.0 \
    -device usb-tablet,bus=usb0.0 \
    $SERIAL_ARGS \
    $DISPLAY_ARGS \
    -boot "order=d,menu=on"
