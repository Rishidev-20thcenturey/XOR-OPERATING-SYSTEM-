# ADR 0003 — FD-anchored filesystem enforcement (not string checks)

**Decision:** Policy canonicalises paths for *deciding*, but the
executor enforces scope by opening the granted directory and walking
components with `dir_fd` + `O_NOFOLLOW`; file grants anchor at the
granted file's parent. Symlinks anywhere on an enforced path fail
closed. File-selection grants may pin `(st_dev, st_ino)`.

**Why:** string-prefix checks are famously bypassed by `..`, symlink
swaps and check/use races. Anchoring to open FDs makes the kernel
itself keep us inside the grant even if path names change mid-flight.

**Consequences / known residual:** a hard link inside a granted
directory can alias an inode that also exists outside it; inode
pinning covers pinned file grants, and the general case is documented
in `broker/src/xor_broker/paths.py` and the threat model (T5) rather
than hidden.
