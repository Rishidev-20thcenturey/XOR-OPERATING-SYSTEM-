#!/usr/bin/env python3
"""Cooperating first-party tool example: writes ONLY through the broker.
The broker will ask the user to confirm before anything is written.

Run the daemon first:  xor-broker serve --manifest <manifest>
Then:                  python3 demo_writer.py ~/XOR-Demo/summary.txt "text..."
"""
import sys

from xor_broker.config import default_socket_path
from xor_broker.server import BrokerClient


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    client = BrokerClient(tool="demo-writer", socket_path=default_socket_path())
    resp = client.write_file(sys.argv[1], sys.argv[2].encode(),
                             description="demo-writer example write")
    print(f"status={resp['status']} reason={resp['reason']}")
    return 0 if resp["status"] == "executed" else 3


if __name__ == "__main__":
    raise SystemExit(main())
