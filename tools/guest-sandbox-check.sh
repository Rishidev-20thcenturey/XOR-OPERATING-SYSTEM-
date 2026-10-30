#!/bin/sh
# Run inside the XOR live guest (as root, via qemu-guest-agent) after the
# desktop is up.  It launches demo-reader through xor-broker and proves the
# bubblewrap mount/network boundary plus one allowed and one denied read.
set -eu

BROKER=/usr/local/bin/xor-broker
TOKEN=/home/user/.local/state/xor/broker/tokens/demo-reader
SOCKET=/run/user/1000/xor-broker/broker.sock
NOTES=/home/user/XOR-Demo/notes.txt
SECRET=/home/user/XOR-Demo/secret.txt

# The session autostart creates the broker, tokens and demo files.  Give it
# time to finish before failing the gate.
i=0
while [ "$i" -lt 60 ]; do
    if [ -S "$SOCKET" ] && [ -f "$TOKEN" ] && [ -f "$NOTES" ]; then
        break
    fi
    i=$((i + 1))
    sleep 1
done
test -S "$SOCKET"
test -f "$TOKEN"
test -f "$NOTES"

# Host-only objects the sandbox must not see.  The secret is a real file
# outside demo-reader's grant, so the broker denial is not just a missing path.
printf 'host-only\n' > /tmp/xor-host-marker
printf 'must-not-be-read\n' > "$SECRET"
chown user:user "$SECRET"

runuser -u user -- env \
    HOME=/home/user \
    XDG_RUNTIME_DIR=/run/user/1000 \
    XDG_STATE_HOME=/home/user/.local/state \
    "$BROKER" launch --tool demo-reader -- /bin/sh -c '
set -eu
test -S /run/xor-broker.sock
test -f /run/xor-token
test ! -e /etc/passwd
test ! -e /home/user/.ssh
test ! -e /tmp/xor-host-marker
test ! -e /run/user/1000
if grep -qE "eth0|enp|wlan" /proc/net/dev; then
    echo "network_interface_visible"
    exit 1
fi
echo "sandbox_boundary_ok"
/usr/local/bin/xor-demo-reader /home/user/XOR-Demo/notes.txt
if /usr/local/bin/xor-demo-reader /home/user/XOR-Demo/secret.txt; then
    echo "ungranted_read_allowed"
    exit 1
fi
echo "ungranted_read_denied"
'
