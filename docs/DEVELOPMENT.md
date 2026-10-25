# Development guide

## Setup ($0, ~2 minutes)

```sh
git clone <repo> && cd XOR-OPERATING-SYSTEM-
tools/test.sh        # needs only python3 >= 3.9; pytest optional
tools/validate.sh
PYTHONPATH=broker/src python3 -m xor_broker.cli demo   # type: approve
```

Optional dev tools: `python3 -m pip install --user pytest pyflakes`,
`apt install shellcheck qemu-system-x86 ovmf live-build`.

## The broker without the ISO

```sh
PYTHONPATH=broker/src python3 -m xor_broker.cli init     # demo files+grants
PYTHONPATH=broker/src python3 -m xor_broker.cli serve    # daemon (CLI confirm)
# second terminal, a cooperating tool:
PYTHONPATH=broker/src python3 broker/examples/demo_reader.py ~/XOR-Demo/notes.txt
PYTHONPATH=broker/src:desktop python3 desktop/xor_activity.py --text
```

Config lives under XDG dirs (`~/.config/xor/broker/manifest.json`,
`~/.local/state/xor/broker/audit.jsonl`, `$XDG_RUNTIME_DIR/xor-broker.sock`),
overridable via flags/env — see `broker/src/xor_broker/config.py`.

## Building & booting the OS image (team machine)

```sh
sudo tools/build-iso.sh        # output: os/live-build/*.iso (+ .sha256, BUILD-MANIFEST.txt)
tools/run-qemu.sh              # fixed target: q35, 2 vCPU, 4 GiB, virtio-VGA/blk/net,
                               # USB KVM+tablet, serial -> xor-serial.log
tools/run-qemu.sh --headless   # serial console in your terminal (add console=ttyS0 at boot menu)
tools/run-qemu.sh --fresh      # recreate the 16G qcow2 first (xor-QEMU-disk.qcow2)
```

Live user `user`, password `live` if prompted (autologin on). In the
session: **XOR Broker Demo** and **XOR Activity** in the menu; the
broker daemon autostarts (`xor-broker serve --confirm gtk`).

Debugging a failed boot: `--headless`, edit the boot line to add
`console=ttyS0`, read `xor-serial.log`; or QEMU monitor with
Ctrl+Alt+2. KVM missing ⇒ TCG emulation (works, slow).

## Where new work belongs

| Change | Place |
|---|---|
| Authorisation rule | `broker/.../policy.py` + unit test + corpus scenario if security-relevant |
| New broker operation | `models.py` (enum), `executor.py`, manifest support, tests — and a threat-model note |
| Confirmation look/feel | `confirm_gtk.py` / `confirm.py` only (never weaken deny-default) |
| Log viewing | `desktop/activity_core.py` (logic) → `xor_activity.py` (UI) |
| OS contents | `os/live-build/config/package-lists/` + hook; never hand-stage `opt/xor-dist` |
| Assistant | `assistant/` — keep disabled until the M3 gate in `ROADMAP.md` |

Rules: stdlib-only for the broker unless an ADR says otherwise;
every security-relevant change ships a test; update
`docs/TEST_RESULTS.md` with actual numbers, including failures.

## Repository map

`broker/` the product core · `desktop/` viewer/theme/launchers ·
`assistant/` disabled stub · `security/` corpus · `os/live-build/`
image · `tools/` build/run/test/validate · `docs/` + `docs/adr/`.
