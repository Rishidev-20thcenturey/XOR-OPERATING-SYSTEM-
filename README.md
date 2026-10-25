# XOR OS

A trustworthy desktop OS in the making: Debian GNU/Linux + XFCE, with
an original **permission broker** at its core. Applications — and one
day an AI assistant — can only touch exactly the files you explicitly
grant them, every write needs your one-time approval, and every
request is written to a tamper-evident local audit log.

This repository is the **first runnable baseline** (Milestones 0–1
complete, 2 in progress). It is a foundation to build on, not a
finished operating system — and it says so wherever that's the case.

## The 60-second demo (no install, no root, no network)

```sh
git clone <this repo> && cd XOR-OPERATING-SYSTEM-
PYTHONPATH=broker/src python3 -m xor_broker.cli demo   # type 'approve' once
```

You'll see: a granted read succeed, a sibling-file read and a
`../` traversal denied, a write paused for your confirmation, a
network send refused as unsupported — then the audit trail of all five.

```sh
tools/test.sh        # unit tests + 200-scenario security corpus
tools/validate.sh    # static checks
```

## Building & running the OS

```sh
sudo tools/build-iso.sh   # Debian 13 + XFCE live ISO (needs live-build, mirror access)
tools/run-qemu.sh         # boot it: q35 · virtio-VGA/blk/net · USB input · serial log
```

Live user `user` (password `live`). In the desktop you'll find
**XOR Broker Demo** and **XOR Activity** (audit viewer); the broker
autostarts per session. Full guide: `docs/DEVELOPMENT.md`.

## What's inside

| Directory | What it is |
|---|---|
| `broker/` | The permission broker: default-deny policy, FD-anchored enforcement, per-write confirmation, hash-chained audit, socket daemon + CLI (Python stdlib only) |
| `desktop/` | Audit activity viewer (GTK + terminal), XFCE theme, wallpaper, launchers |
| `assistant/` | Future assistant connection point — **disabled stub**; no model, no network |
| `security/` | 200 deterministic adversarial scenarios + acceptance runner (all passing) |
| `os/live-build/` | Debian Live Build configuration for the bootable image |
| `tools/` | `build-iso.sh`, `run-qemu.sh`, `test.sh`, `validate.sh` |
| `docs/` | Architecture, threat model, testing, roadmap, update design, ADRs, test results |

## Principles

* **Reuse, don't reinvent** — Linux kernel, Debian packages, XFCE.
* **Default-deny** — nothing is granted implicitly, ever; what we
  can't enforce safely is refused, not faked.
* **Security before AI** — the assistant stays off until the broker
  and its isolation are proven.
* **Honesty** — limits are documented (`docs/THREAT_MODEL.md`), test
  results are published as-is (`docs/TEST_RESULTS.md`).

License: MIT for XOR code (`LICENSE`); image contents under their own
Debian licenses. Contributing: `CONTRIBUTING.md`.
