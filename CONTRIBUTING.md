# Contributing to XOR OS

Welcome — this project is built by a small team of friends, and it's
designed so no single person (or AI) is a bottleneck.

## Get to green first

```sh
tools/test.sh && tools/validate.sh
```

Both must pass before and after your change. Python ≥3.9, nothing else
required. See `docs/DEVELOPMENT.md` for the full setup and ISO build.

## Ground rules

1. **Tests travel with code.** Security-relevant changes need a unit
   test and, where applicable, a corpus scenario (`security/`).
2. **Fail closed.** If a change makes you choose between convenience
   and denying, deny — then document the trade-off.
3. **No simulated success.** Unfinished features say so (in UI, docs
   and code). Never claim enforcement we don't have; check
   `docs/THREAT_MODEL.md` before making security claims.
4. **Stdlib first.** New broker dependencies need an ADR in `docs/adr/`.
5. **Small, reviewable changes.** One component boundary per PR where
   possible (see the map in `docs/DEVELOPMENT.md`).
6. **Record results honestly.** Update `docs/TEST_RESULTS.md` with the
   actual output, including failures and skips with reasons.

## Workflow

Branch → change → `tools/test.sh` + `tools/validate.sh` → PR with a
short description and the test evidence → one teammate reviews →
merge. Significant design choices get a numbered ADR.

Security bugs: follow `SECURITY.md` instead of opening a public issue.
