# ADR 0004 — Hash-chained JSONL audit log, digests only

**Decision:** One append-only JSONL file (0600 in a 0700 dir). Each
record: seq, event/request ids, UTC timestamp, tool, operation,
canonical resource, **content digest (never content)**, decision,
reason, confirmation outcome, result, capability id, plus
`prev_hash`/`hash` (SHA-256) forming a chain verifiable offline.

**Why:** accountability is a product feature ("show me what was
approved"). JSONL is inspectable with plain tools; the chain makes
after-the-fact edits, deletions and reordering detectable without a
daemon or database.

**Consequences / honest limits:** a local chain is tamper-*evidence*,
not tamper-proofing — whoever controls log + verifier (root) can forge
consistency. Anchoring the head hash externally is Milestone-4
hardening. Log growth/rotation policy must be designed before the log
is used at scale (rotation must preserve chain verifiability).
