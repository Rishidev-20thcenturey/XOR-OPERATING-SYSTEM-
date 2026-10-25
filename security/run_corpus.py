#!/usr/bin/env python3
"""Run the 200-scenario corpus against a fresh broker per scenario and
evaluate the Milestone-1 acceptance criteria.  Exits non-zero if any
criterion fails.  Prints the actual numbers - never adjusted to pass.

For injection scenarios the runner also executes a *twin* request with
the untrusted text removed (in a second fresh sandbox) and requires an
identical outcome status: untrusted text must be decision-irrelevant.
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "broker", "src"))

from xor_broker.audit import AuditLog, verify_chain  # noqa: E402
from xor_broker.broker import Broker  # noqa: E402
from xor_broker.confirm import ConfirmationManager, ScriptedConfirmation  # noqa: E402
from xor_broker.manifest import parse_manifest  # noqa: E402
from xor_broker.models import ActionRequest, Operation  # noqa: E402
from xor_broker.policy import PolicyEngine  # noqa: E402

UNSUPPORTED = {"network_send", "delete_file", "exec_process"}


def subst(obj, sb):
    if isinstance(obj, str):
        return obj.replace("$SB", sb)
    if isinstance(obj, list):
        return [subst(x, sb) for x in obj]
    if isinstance(obj, dict):
        return {k: subst(v, sb) for k, v in obj.items()}
    return obj


def make_request(r):
    return ActionRequest(
        tool=r["tool"], operation=Operation(r["operation"]), path=r["path"],
        content=r["content"].encode() if r.get("content") is not None else None,
        description=r.get("description", ""),
        untrusted_context=r.get("untrusted_context"))


def run_scenario(scn, strip_untrusted=False):
    """Returns dict of observations for one scenario execution."""
    with tempfile.TemporaryDirectory(prefix="xor-corpus-") as sb:
        scn = subst(scn, sb)
        markers = []
        for rel, content in scn["setup"].get("files", {}).items():
            path = os.path.join(sb, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as fh:
                fh.write(content)
            markers.append(content)
        for rel, target in scn["setup"].get("symlinks", {}).items():
            link = os.path.join(sb, rel)
            os.makedirs(os.path.dirname(link), exist_ok=True)
            os.symlink(target, link)
        if scn["request"].get("content"):
            markers.append(scn["request"]["content"])

        confirm_default = (scn.get("confirm") == "approve")
        provider = ScriptedConfirmation(default=confirm_default)
        audit_path = os.path.join(sb, "audit.jsonl")
        broker = Broker(
            PolicyEngine(parse_manifest({"capabilities": scn["grants"]})),
            ConfirmationManager(provider),
            AuditLog(audit_path))

        n_requests = 0
        for pre in scn.get("pre_requests", []):
            broker.handle(make_request(pre))
            n_requests += 1

        request = make_request(scn["request"])
        if strip_untrusted:
            request.untrusted_context = None
        write_snapshot = None
        if request.operation is Operation.WRITE_FILE:
            # Snapshot the *real* would-be target (resolving symlinks) so a
            # write-through-symlink side effect is measured correctly.
            real_target = os.path.realpath(request.path)
            if os.path.exists(real_target):
                with open(real_target, "rb") as fh:
                    write_snapshot = fh.read()
        prompts_before = len(provider.seen)
        outcome = broker.handle(request)
        n_requests += 1

        write_changed = None
        if request.operation is Operation.WRITE_FILE:
            target = os.path.realpath(request.path)
            after = None
            if os.path.exists(target):
                with open(target, "rb") as fh:
                    after = fh.read()
            write_changed = (after != write_snapshot)

        with open(audit_path) as fh:
            audit_raw = fh.read()
        chain_ok, chain_n, _ = verify_chain(audit_path)
        return {
            "status": outcome.status.value,
            "executed": outcome.status.value == "executed",
            "prompt_shown": len(provider.seen) > prompts_before,
            "write_changed": write_changed,
            "audit_records": audit_raw.count("\n"),
            "expected_records": n_requests,
            "chain_ok": chain_ok and chain_n == n_requests,
            "content_leaked": any(m and m in audit_raw for m in markers),
            "data": outcome.data.decode() if outcome.data else None,
        }


def main():
    with open(os.path.join(HERE, "corpus", "scenarios.json")) as fh:
        corpus = json.load(fh)
    scenarios = corpus["scenarios"]

    failures = []
    stats = {"oog_executed": 0, "network_executed": 0, "writes_without_prompt": 0,
             "audit_failures": 0, "content_leaks": 0, "injection_decision_changed": 0,
             "legit_approved_total": 0, "legit_approved_ok": 0,
             "unsupported_total": 0, "unsupported_not_flagged": 0,
             "expectation_mismatches": 0, "write_side_effects": 0}

    for scn in scenarios:
        obs = run_scenario(scn)
        expected = scn["expect"]["executed"]
        op = scn["request"]["operation"]

        if obs["executed"] != expected:
            stats["expectation_mismatches"] += 1
            failures.append(f"{scn['id']} ({scn['kind']}): executed={obs['executed']} "
                            f"expected={expected} status={obs['status']}")
        if not expected and obs.get("write_changed"):
            stats["write_side_effects"] += 1
            failures.append(f"{scn['id']}: denied write still modified the target")
        if scn["category"] == "out_of_grant" and obs["executed"]:
            stats["oog_executed"] += 1
        if op == "network_send" and obs["executed"]:
            stats["network_executed"] += 1
        if op == "write_file" and obs["executed"] and not obs["prompt_shown"]:
            stats["writes_without_prompt"] += 1
        if not obs["chain_ok"] or obs["audit_records"] != obs["expected_records"]:
            stats["audit_failures"] += 1
            failures.append(f"{scn['id']}: audit records={obs['audit_records']} "
                            f"expected={obs['expected_records']} chain_ok={obs['chain_ok']}")
        if obs["content_leaked"]:
            stats["content_leaks"] += 1
            failures.append(f"{scn['id']}: raw content found in audit log")
        if op in UNSUPPORTED:
            stats["unsupported_total"] += 1
            if obs["status"] != "denied_unsupported":
                stats["unsupported_not_flagged"] += 1
                failures.append(f"{scn['id']}: unsupported op status={obs['status']}")
        if scn["category"] == "legit" and scn["expect"]["executed"]:
            stats["legit_approved_total"] += 1
            if obs["executed"]:
                stats["legit_approved_ok"] += 1
        if scn["category"] == "injection":
            twin = run_scenario(scn, strip_untrusted=True)
            if twin["status"] != obs["status"]:
                stats["injection_decision_changed"] += 1
                failures.append(f"{scn['id']}: untrusted text changed decision "
                                f"({obs['status']} vs twin {twin['status']})")

    legit_rate = (stats["legit_approved_ok"] / stats["legit_approved_total"]
                  if stats["legit_approved_total"] else 0.0)
    criteria = [
        ("1. Zero out-of-grant actions execute", stats["oog_executed"] == 0,
         f"{stats['oog_executed']} executed"),
        ("2. Zero unauthorised network sends", stats["network_executed"] == 0,
         f"{stats['network_executed']} executed"),
        ("3. Every executed write was confirmed first",
         stats["writes_without_prompt"] == 0,
         f"{stats['writes_without_prompt']} unconfirmed"),
        ("4. Every request produced a valid audit record",
         stats["audit_failures"] == 0, f"{stats['audit_failures']} failures"),
        ("5. No raw file content in audit logs", stats["content_leaks"] == 0,
         f"{stats['content_leaks']} leaks"),
        ("6. >=95% of legitimate approved actions succeed", legit_rate >= 0.95,
         f"{stats['legit_approved_ok']}/{stats['legit_approved_total']} = {legit_rate:.1%}"),
        ("7. Unsupported ops identified, never faked as success",
         stats["unsupported_not_flagged"] == 0,
         f"{stats['unsupported_total']} unsupported requests, "
         f"{stats['unsupported_not_flagged']} mislabelled"),
        ("8. Untrusted text never changes a decision (twin check)",
         stats["injection_decision_changed"] == 0,
         f"{stats['injection_decision_changed']} changed"),
        ("9. No denied write had side effects", stats["write_side_effects"] == 0,
         f"{stats['write_side_effects']} side effects"),
        ("10. All scenario expectations met", stats["expectation_mismatches"] == 0,
         f"{stats['expectation_mismatches']} mismatches"),
    ]

    print(f"Corpus: {len(scenarios)} scenarios "
          f"(+{sum(1 for s in scenarios if s['category'] == 'injection')} injection twins)\n")
    all_ok = True
    for name, ok, detail in criteria:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}  ({detail})")
        all_ok &= ok
    if failures:
        print("\nFailures:")
        for f in failures[:30]:
            print(f"  - {f}")
    summary = {"scenarios": len(scenarios), "stats": stats,
               "legit_rate": round(legit_rate, 4),
               "criteria": [{"name": n, "pass": ok, "detail": d} for n, ok, d in criteria],
               "failures": failures}
    out = os.path.join(HERE, "corpus", "last_results.json")
    with open(out, "w") as fh:
        json.dump(summary, fh, indent=2)
    print(f"\nResults written to {out}")
    print("OVERALL:", "PASS" if all_ok else "FAIL")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
