# Update & recovery design (design only — not implemented)

Status: **no updater exists in the baseline.** Nothing here is signed,
verified, switched or rolled back today. This document is the contract
the Milestone-3 prototype must satisfy before any such claim is made.

## Direction: A/B (two-slot) system images

```
        ┌─────────────┐   verify OK    ┌─────────────┐
 boot ──►  slot A      │──────────────► │  slot B      │ (new, inactive→active)
 (known   │  RUNNING    │   mark-good    │  candidate   │
  good)   └─────────────┘◄─── rollback └─────────────┘
                 on failed boot / failed health check
 /home, /etc overrides and broker state live OUTSIDE the slots
 (persistent partition), so slot switches never touch user data,
 capability manifests or the audit log.
```

Update flow: download/build candidate → write to **inactive** slot →
verify (below) → set next-boot flag → reboot once into candidate →
health check (broker corpus smoke test + desktop session reaches
LightDM/XFCE) → mark-good; anything else reboots back to the old slot.

## Integrity & signing (to implement, then test, then claim)

* Images signed offline with an XOR release key (minisign-style Ed25519
  detached signature over the image manifest); public key baked into
  the OS image at build. Candidate rejected on any signature/hash
  mismatch — before it is ever marked bootable.
* Manifest lists per-file hashes so partial/corrupt writes are caught.
* **Downgrade policy:** refuse images whose version counter is below
  the last marked-good counter stored in the bootloader environment.
  We will not rely on wall-clock time (clock can be set backwards);
  a monotonic counter + hardware-backed storage (TPM NVRAM) is the
  long-term answer, bootloader env is the prototype answer, with its
  limits written down.

## Failure matrix the Milestone-3 prototype must survive (QEMU fault injection)

| Fault | Expected behaviour |
|---|---|
| Power loss during slot write | Old slot still boots; candidate discarded |
| Corrupted download (bit flip) | Hash mismatch → reject, stay on old slot |
| Forged/unsigned image | Signature mismatch → reject + auditable event |
| Candidate kernel panics / no desktop | Boot counter exhausts → auto-rollback |
| Candidate boots but broker corpus fails | Health check fails → rollback, report kept |
| Older (validly signed) image offered | Refused by version-counter policy |
| Interrupted flag flip (next-boot set, no reboot) | One-shot flag semantics; next normal boot unaffected |

## Non-goals for the prototype

Delta updates, background silent installs (updates need the same
user-consent discipline as broker writes), and TPM-sealed boot — all
later milestones. Kernel/Debian security updates between XOR releases
continue via the normal Debian apt path on installed systems; the A/B
mechanism covers XOR system images.
