# Security policy

## Reporting

Found a broker bypass, audit-log weakness or other security issue?
Please **do not open a public issue**. Use GitHub private
vulnerability reporting for this repository (Security tab → "Report a
vulnerability"), or contact a maintainer directly. We're a small
volunteer team: expect an acknowledgement within about a week.

Good reports include a minimal reproduction — ideally a failing unit
test or a new scenario for `security/generate_corpus.py`.

## Scope honesty

This baseline mediates requests made through the broker API; it does
not confine arbitrary same-user processes (see
`docs/THREAT_MODEL.md`, especially T11–T13). Reports that assume
system-wide enforcement are still valuable as roadmap input, but may
be reclassified as hardening tasks rather than vulnerabilities.

There is no bounty program (we're unfunded) — just credit in the
release notes and our thanks.
