#!/usr/bin/env python3
"""Static validation of the XOR baseline (no privileges/display needed).

Checks — each reports PASS/FAIL, exit code is non-zero on any FAIL:
  * every Python source compiles; pyflakes clean if installed
  * JSON validity: example manifest (through the real validator) + corpus
  * corpus has exactly 100/50/50 scenarios and regenerate->identical
  * XFCE theme XML is well-formed; .desktop entries have required keys
  * live-build config + hook + package list present; shell scripts parse
    (sh -n) and shellcheck-clean if shellcheck is installed
  * wallpaper is a real PNG of expected dimensions
Run: tools/validate.sh  (or python3 tools/validate.py)
"""
import json
import os
import py_compile
import shutil
import struct
import subprocess
import sys
import xml.etree.ElementTree as ET

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "broker", "src"))

results = []


def check(name, fn):
    try:
        detail = fn() or ""
        results.append((True, name, detail))
    except Exception as exc:  # noqa: BLE001 - report, don't crash validation
        results.append((False, name, f"{type(exc).__name__}: {exc}"))


def py_sources():
    for base in ("broker/src", "broker/tests", "broker/examples", "desktop",
                 "assistant/src", "assistant/tests", "security", "tools"):
        for root, _dirs, files in os.walk(os.path.join(REPO, base)):
            for f in files:
                if f.endswith(".py"):
                    yield os.path.join(root, f)


def t_compile():
    files = list(py_sources())
    for f in files:
        py_compile.compile(f, doraise=True)
    return f"{len(files)} files"


def t_pyflakes():
    if shutil.which("pyflakes") is None and subprocess.run(
            [sys.executable, "-c", "import pyflakes"],
            capture_output=True).returncode != 0:
        return "SKIPPED (pyflakes not installed)"
    out = subprocess.run([sys.executable, "-m", "pyflakes", *py_sources()],
                         capture_output=True, text=True)
    # Pre-existing known-tolerated: star imports none; treat output as failure.
    assert out.returncode == 0, out.stdout + out.stderr
    return "clean"


def t_manifest():
    from xor_broker.manifest import load_manifest
    caps = load_manifest(os.path.join(REPO, "broker/examples/manifest.example.json"))
    assert len(caps) == 3
    return f"{len(caps)} example grants valid"


def t_corpus():
    path = os.path.join(REPO, "security/corpus/scenarios.json")
    with open(path) as fh:
        corpus = json.load(fh)
    assert corpus["counts"] == {"out_of_grant": 100, "injection": 50, "legit": 50}, corpus["counts"]
    assert len(corpus["scenarios"]) == 200
    # Determinism: regenerate and compare bytes.
    with open(path, "rb") as fh:
        before = fh.read()
    subprocess.run([sys.executable, os.path.join(REPO, "security/generate_corpus.py")],
                   check=True, capture_output=True)
    with open(path, "rb") as fh:
        after = fh.read()
    assert before == after, "corpus generation is NOT deterministic"
    return "200 scenarios; regeneration byte-identical"


def t_xml():
    theme = os.path.join(REPO, "desktop/theme/xfce4/xfconf/xfce-perchannel-xml")
    names = [f for f in os.listdir(theme) if f.endswith(".xml")]
    for f in names:
        ET.parse(os.path.join(theme, f))
    assert names, "no XFCE xml found"
    return f"{len(names)} XML files well-formed"


def t_desktop_files():
    d = os.path.join(REPO, "desktop/applications")
    files = [f for f in os.listdir(d) if f.endswith(".desktop")]
    assert len(files) >= 3
    for f in files:
        text = open(os.path.join(d, f)).read()
        for key in ("[Desktop Entry]", "Type=", "Name=", "Exec="):
            assert key in text, f"{f} missing {key}"
    return f"{len(files)} entries have required keys"


def t_livebuild_tree():
    base = os.path.join(REPO, "os/live-build")
    required = [
        "auto/config", "auto/build", "auto/clean",
        "config/package-lists/xor.list.chroot",
        "config/hooks/normal/0900-xor-install.hook.chroot",
        "config/includes.chroot/etc/lightdm/lightdm.conf.d/50-XOR.conf",
    ]
    for rel in required:
        assert os.path.isfile(os.path.join(base, rel)), f"missing {rel}"
    config = open(os.path.join(base, "auto/config")).read()
    for needle in ("trixie", "amd64", "iso-hybrid"):
        assert needle in config, f"auto/config lacks {needle}"
    return "config, hook, package list present"


def t_shell():
    scripts = [
        "tools/build-iso.sh", "tools/run-qemu.sh", "tools/test.sh",
        "os/live-build/auto/config", "os/live-build/auto/build",
        "os/live-build/auto/clean",
        "os/live-build/config/hooks/normal/0900-xor-install.hook.chroot",
        "desktop/scripts/xor-broker-autostart.sh",
        "desktop/scripts/xor-broker-demo-run.sh",
    ]
    for rel in scripts:
        out = subprocess.run(["sh", "-n", os.path.join(REPO, rel)],
                             capture_output=True, text=True)
        assert out.returncode == 0, f"{rel}: {out.stderr}"
    detail = f"{len(scripts)} scripts parse"
    if shutil.which("shellcheck"):
        out = subprocess.run(["shellcheck", "-s", "sh"] +
                             [os.path.join(REPO, s) for s in scripts],
                             capture_output=True, text=True)
        assert out.returncode == 0, out.stdout
        detail += "; shellcheck clean"
    else:
        detail += "; shellcheck SKIPPED (not installed)"
    return detail


def t_wallpaper():
    path = os.path.join(REPO, "desktop/theme/wallpaper.png")
    with open(path, "rb") as fh:
        head = fh.read(24)
    assert head[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    width, height = struct.unpack(">II", head[16:24])
    assert (width, height) == (1920, 1080), f"{width}x{height}"
    return "1920x1080 PNG"


def main():
    check("python sources compile", t_compile)
    check("pyflakes", t_pyflakes)
    check("example manifest validates", t_manifest)
    check("corpus structure + determinism", t_corpus)
    check("XFCE theme XML well-formed", t_xml)
    check(".desktop entries", t_desktop_files)
    check("live-build tree complete", t_livebuild_tree)
    check("shell scripts", t_shell)
    check("wallpaper PNG", t_wallpaper)

    ok_all = True
    for ok, name, detail in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}  ({detail})")
        ok_all &= ok
    print("\nOVERALL:", "PASS" if ok_all else "FAIL")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
