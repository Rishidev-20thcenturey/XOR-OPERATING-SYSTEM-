#!/bin/sh
# Visible broker demonstration launched from the applications menu.
# Runs entirely inside ~/XOR-Demo-Run; one step asks you to confirm a
# write (type: approve).  Ends by showing the audit trail.
set -u
echo "XOR permission broker demonstration"
echo "==================================="
xor-broker demo --keep "$HOME/XOR-Demo-Run"
echo
printf "Press Enter to close... "
read -r _dummy
