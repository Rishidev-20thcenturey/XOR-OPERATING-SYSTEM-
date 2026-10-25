# Broker security corpus

Deterministic adversarial test harness for the permission broker.

```sh
python3 security/generate_corpus.py   # regenerate scenarios.json (byte-identical)
python3 security/run_corpus.py        # execute + acceptance criteria, exit 0/1
```

Corpus (exactly 200): 100 out-of-grant attempts (sibling reads,
parent-dir inference, traversal spellings, symlink escapes incl.
write-through, wrong tool, expired/exhausted grants, read-grant
misused for write, network/delete/exec), 50 prompt-injection-style
scenarios (document text demanding actions, fake approvals, attempts
to alter legitimate reads), 50 legitimate actions (45 approved,
5 user-denied writes).

The runner builds a fresh sandbox + broker per scenario and checks:
expectations, one valid hash-chained audit record per request, zero
content leakage into the log, confirmation shown before any executed
write, zero side effects from denied writes, unsupported ops flagged
honestly — and for injection scenarios, a **twin run** without the
untrusted text must yield the identical decision.

Scope honesty: no LLM exists in the baseline, so injections are tested
as *broker inputs* (simulated tool requests carrying hostile text).
This proves the authorisation boundary ignores untrusted text; it is
not a claim about any future model's behaviour.
