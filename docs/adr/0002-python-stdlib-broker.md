# ADR 0002 — Broker in Python, standard library only

**Decision:** Implement the permission broker in Python ≥3.9 using only
the standard library. GTK (`gi`) is imported lazily by the optional
confirmation provider and is never a broker-core dependency.

**Why:** the team (beginner→intermediate) must be able to read every
line of the security boundary. Stdlib-only means zero supply-chain
surface to audit, no pip step on any machine or CI, and tests run
anywhere Python exists. Performance needs are tiny (requests are
human-paced).

**Consequences:** if a future component needs a dependency, it needs an
ADR justifying it. A Rust/Go rewrite remains possible later behind the
same wire protocol (`server.py`) — the protocol, not the language, is
the contract.
