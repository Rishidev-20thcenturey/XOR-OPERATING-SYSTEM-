#!/usr/bin/env python3
"""Cooperating first-party tool example: reads ONLY through the broker.

Run the daemon first:  xor-broker serve --manifest <manifest>
Then:                  python3 demo_reader.py ~/XOR-Demo/notes.txt
Reading anything not granted to 'demo-reader' is denied and audited.
"""
import sys

from xor_broker.config import default_socket_path
from xor_broker.server import BrokerClient, BrokerError


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    client = BrokerClient(tool="demo-reader", socket_path=default_socket_path())
    try:
        data = client.read_file(sys.argv[1])
    except BrokerError as exc:
        print(f"Broker refused the read: {exc}")
        return 3
    sys.stdout.write(data.decode("utf-8", errors="replace"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
