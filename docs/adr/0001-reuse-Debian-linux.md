# ADR 0001 — Reuse the Linux kernel via Debian; XFCE as the baseline desktop

**Decision:** Build XOR OS as a Debian 13 (trixie) x86-64 live system
(Debian Live Build) with XFCE. No custom kernel, drivers, package
manager, browser engine or desktop environment in the baseline.

**Why:** a small team at $0 cannot reproduce decades of kernel/driver
work; Debian gives a maintained, signed, security-updated base and a
huge package archive. XFCE is light, configurable via files and runs
well in QEMU — a sound baseline while the original XOR experience is
designed. Differentiation goes where it matters: the permission broker.

**Consequences:** we track Debian stable; ISO builds need mirror access
and root, so they happen on team machines, not restricted sandboxes.
Replacing XFCE later must not require broker changes (enforced by the
module boundaries in `docs/ARCHITECTURE.md`).
