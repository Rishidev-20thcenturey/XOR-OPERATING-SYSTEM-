# Testing guide

Everything below runs unprivileged, headless, offline, with stock
`python3` (≥3.9). `pytest` gives nicer output; `unittest` works too.

```sh
tools/test.sh        # unit tests + security corpus (the whole suite)
tools/validate.sh    # static checks: compile, pyflakes, XML/JSON/shell...
```

## Layers

| Layer | Where | What it proves |
|---|---|---|
| Policy/paths unit tests | `broker/tests/test_policy_and_paths.py` | default-deny matrix, scope exactness, traversal/symlink fail-closed, expiry/exhaustion, untrusted-text irrelevance |
| Broker end-to-end | `broker/tests/test_broker_e2e.py` | confirmation binding/staleness/replay, deny-by-default CLI, audit completeness + redaction + tamper detection, manifest rejection, real Unix-socket round-trip |
| Security corpus | `security/` (200 scenarios) | Milestone-1 acceptance criteria, below |
| Assistant gate | `assistant/tests/` | assistant disabled; declared surface read-only & broker-mapped; no direct file/network APIs in the stub |
| Viewer core | `desktop/tests/` | audit loading/filter/search/chain status behind the GTK UI |
| Static validation | `tools/validate.py` | 28 Python files compile + pyflakes, corpus byte-deterministic, theme XML well-formed, `.desktop` keys, shell `sh -n`, wallpaper PNG 1920×1080 |

## The 200-scenario corpus

`security/generate_corpus.py` deterministically emits
`security/corpus/scenarios.json`: **100 out-of-grant** attempts,
**50 prompt-injection-style** scenarios, **50 legitimate** actions
(45 approve-intended, 5 deliberately user-denied).
`security/run_corpus.py` executes each against a fresh broker/sandbox
and checks expectations, audit records, hash chain, content redaction,
prompt-before-write, and side-effect-freeness of denied writes.
Injection scenarios additionally run a **twin** with the untrusted text
stripped and require an identical decision.

Honest scope: there is no LLM in the baseline. These tests prove the
*broker* treats document/tool-output text as decision-irrelevant; they
say nothing about how a future model behaves. That distinction is
deliberate.

### Milestone-1 acceptance criteria (checked by the runner)

1. Zero out-of-grant actions execute
2. Zero unauthorised network sends
3. Every executed write was confirmed first
4. Every request yields a valid, hash-chained audit record
5. No raw file content in audit logs
6. ≥95% of legitimate approved actions succeed
7. Unsupported ops reported as `denied_unsupported`, never faked
8. (extra) Untrusted text never changes a decision — twin check
9. (extra) Denied writes cause zero filesystem side effects
10. (extra) All scenario expectations met

Latest numbers, including environment and what did **not** run, live in
`docs/TEST_RESULTS.md`. Skips are reported as skips; we never edit a
test to hide a failure — failures get fixed in product code or the
expectation is corrected with a written justification.

## Not covered yet

* GTK dialogs (need a display): `confirm_gtk.py`, `xor_activity.py` GTK
  mode are compile-checked and logic-reviewed; on-desktop keyboard
  verification (Esc denies, Deny focused, Ctrl+1–4 filters) is a
  Milestone-2 checklist item — `xor-activity --text` *is* exercised.
* ISO boot in QEMU (no QEMU/live-build in the dev sandbox):
  run `sudo tools/build-iso.sh && tools/run-qemu.sh` on a team machine
  and record the result in `TEST_RESULTS.md`.
* Fuzzing the manifest/wire parsers, concurrency under load, and
  power-loss/fault-injection for the future updater (Milestones 3–4).
