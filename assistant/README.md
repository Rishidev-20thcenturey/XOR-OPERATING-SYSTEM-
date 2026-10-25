# XOR assistant — disabled integration stub

This is where the future assistant will connect. **Today it does
nothing, on purpose** (Milestone 3 gate, see `docs/ROADMAP.md`):

* No model is downloaded, bundled or called; no cloud APIs, no keys.
* `DisabledAssistant.invoke_tool()/chat()` always raise
  `AssistantDisabled` — there is no flag that switches it on.
* `declared_tools()` documents the *only* future surface: two
  **read-only** tools mapping 1:1 onto broker operations
  (`read_file`, `list_dir`) under tool id `xor-assistant`. No write,
  delete, network or exec tools are declared.
* Tests (`tests/test_disabled.py`) verify the stub is disabled, the
  surface stays read-only, and the module imports no filesystem /
  network / subprocess APIs.

Enabling plan (future): run the assistant isolated (bubblewrap) with
the broker Unix socket as its only path to user data, re-run the full
security corpus against it, and record the decision in an ADR —
including model choice and why it needs no external service.
