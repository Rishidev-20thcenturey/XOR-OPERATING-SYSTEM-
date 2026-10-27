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
import re
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
    assert len(files) >= 7, files
    required_exec = {
        "xor-shell.desktop": "Exec=xor-shell",
        "xor-shell-launcher.desktop": "Exec=xor-shell --launcher",
        "xor-control-center.desktop": "Exec=xor-control-center",
        "xor-shell-autostart.desktop": "Exec=xor-shell-autostart",
    }
    for f in files:
        text = open(os.path.join(d, f)).read()
        for key in ("[Desktop Entry]", "Type=", "Name=", "Exec="):
            assert key in text, f"{f} missing {key}"
    for filename, exec_line in required_exec.items():
        text = open(os.path.join(d, filename)).read()
        assert exec_line in text, f"{filename} lacks {exec_line}"
    return f"{len(files)} entries have required keys + Aurora commands"


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
    # live-build only applies its mirror defaults when --mode is lowercase
    # "debian".  The old "Debian" spelling produced an empty mirror URI and
    # an unreadable /etc/apt/sources.list inside the chroot.
    assert "--mode debian" in config, "auto/config must use lowercase debian mode"
    assert "http://deb.debian.org/debian" in config, "auto/config lacks the Debian mirror"
    assert "http://security.debian.org/debian-security" in config, \
        "auto/config lacks the Debian security mirror"
    packages = open(os.path.join(base, "config/package-lists/xor.list.chroot")).read()
    package_lines = {
        line.strip() for line in packages.splitlines()
        if line.strip() and not line.strip().startswith("#")
    }
    for needle in ("gir1.2-wnck-3.0", "pavucontrol", "procps",
                   "thunar-volman", "fonts-inter", "fonts-dejavu", "polkitd"):
        assert needle in package_lines, f"package list lacks {needle}"
    for legacy in ("Thunar-volman", "fonts-Inter", "fonts-DejaVu", "policykit-1"):
        assert legacy not in package_lines, f"package list retains invalid name {legacy}"
    hook = open(os.path.join(
        base, "config/hooks/normal/0900-xor-install.hook.chroot")).read()
    for needle in ("desktop/xor_shell", "/usr/local/bin/xor-shell",
                   "/usr/local/bin/xor-control-center", "xor-shell-autostart",
                   "desktop/theme/wallpapers/", "xor-logo.svg",
                   "xor_shell --status-json", "xor_shell.control_center --text"):
        assert needle in hook, f"install hook lacks {needle}"
    # Demo tools are exposed through PYTHONPATH wrappers.  Do not mutate
    # their shebang/docstring with sed; both variants have broken before.
    for needle in ("/usr/local/bin/xor-demo-reader",
                   "/usr/local/bin/xor-demo-writer",
                   "/usr/local/lib/xor-broker/examples/demo_reader.py",
                   "PYTHONPATH=/usr/local/lib/xor-broker python3 "
                   "/usr/local/lib/xor-broker/examples/demo_reader.py"):
        assert needle in hook, f"install hook lacks {needle}"
    assert "sed -i '1i import sys" not in hook, "demo-tool import is before the shebang"
    assert "sed -i '1a import sys" not in hook, "demo-tool import displaces the docstring"
    return "config, mirrors, hook, package list + Aurora install wiring present"


def t_qemu_target():
    path = os.path.join(REPO, "tools/run-qemu.sh")
    text = open(path, encoding="utf-8").read()
    # QEMU device names and Linux device nodes are case-sensitive.  These
    # typos are invisible to ShellCheck and would break the first boot.
    assert "/dev/kvm" in text, "run-qemu.sh does not check /dev/kvm"
    assert 'ACCEL="kvm"' in text, "run-qemu.sh does not select the kvm accelerator"
    assert "virtio-vga" in text, "run-qemu.sh lacks virtio-vga"
    assert "usb-kbd" in text, "run-qemu.sh lacks a USB keyboard device"
    for bad in ("/dev/KVM", 'ACCEL="KVM"', "virtio-VGA", "usb-KVM"):
        assert bad not in text, f"run-qemu.sh contains invalid identifier {bad!r}"
    return "KVM, virtio-vga and usb-kbd identifiers are correct"


def t_shell():
    scripts = [
        "tools/build-iso.sh", "tools/run-qemu.sh", "tools/test.sh",
        "tools/test-build-iso.sh",
        "os/live-build/auto/config", "os/live-build/auto/build",
        "os/live-build/auto/clean",
        "os/live-build/config/hooks/normal/0900-xor-install.hook.chroot",
        "desktop/scripts/xor-broker-autostart.sh",
        "desktop/scripts/xor-broker-demo-run.sh",
        "desktop/scripts/xor-shell-autostart.sh",
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


def t_aurora_assets():
    css_path = os.path.join(REPO, "desktop/xor_shell/assets/style.css")
    css = open(css_path, encoding="utf-8").read()
    assert css.count("{") == css.count("}"), "unbalanced CSS braces"
    for needle in (".shell-panel", ".floating-dock", ".launcher-card",
                   ".app-card", ".control-window", "@xor_primary",
                   "@xor_secondary", "button:focus"):
        assert needle in css, f"style.css lacks {needle}"

    # GTK 3 CSS only accepts numeric font-weight values in hundreds.  A
    # non-hundred value raises a GLib.CssProvider error at runtime and stops
    # the entire shell before its first window is created.
    for match in re.finditer(r"font-weight\s*:\s*(\d+)", css):
        weight = int(match.group(1))
        line = css.count("\n", 0, match.start()) + 1
        assert 100 <= weight <= 900 and weight % 100 == 0, (
            f"style.css line {line}: GTK-invalid font-weight {weight}")

    logo = os.path.join(REPO, "desktop/theme/xor-logo.svg")
    root = ET.parse(logo).getroot()
    assert root.tag.endswith("svg"), "logo root is not SVG"

    aurora = os.path.join(REPO, "desktop/theme/wallpapers/xor-royal-aurora.jpg")
    with open(aurora, "rb") as fh:
        magic = fh.read(3)
    assert magic == b"\xff\xd8\xff", "Aurora wallpaper is not JPEG"
    assert os.path.getsize(aurora) > 50_000, "Aurora wallpaper suspiciously small"

    xfce = open(os.path.join(
        REPO, "desktop/theme/xfce4/xfconf/xfce-perchannel-xml/xfce4-desktop.xml"),
        encoding="utf-8").read()
    assert "/usr/share/xor/wallpapers/xor-royal-aurora.jpg" in xfce
    return "CSS, SVG logo, JPEG wallpaper and XFCE default agree"


def t_xor_shell_cli():
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([
        os.path.join(REPO, "desktop"),
        os.path.join(REPO, "broker", "src"),
        env.get("PYTHONPATH", ""),
    ])
    status = subprocess.run(
        [sys.executable, "-m", "xor_shell", "--status-json"],
        capture_output=True, text=True, env=env)
    assert status.returncode == 0, status.stderr
    payload = json.loads(status.stdout)
    assert payload["shell_version"] == "0.2.0"
    assert payload["config"]["wallpaper"] == "xor-royal-aurora.jpg"

    control = subprocess.run(
        [sys.executable, "-m", "xor_shell.control_center", "--text"],
        capture_output=True, text=True, env=env)
    assert control.returncode == 0, control.stderr
    assert "XOR Control Centre" in control.stdout
    assert " GiB memory" in control.stdout
    return "headless shell + control-centre status commands run"


def main():
    check("python sources compile", t_compile)
    check("pyflakes", t_pyflakes)
    check("example manifest validates", t_manifest)
    check("corpus structure + determinism", t_corpus)
    check("XFCE theme XML well-formed", t_xml)
    check(".desktop entries", t_desktop_files)
    check("live-build tree complete", t_livebuild_tree)
    check("QEMU target identifiers", t_qemu_target)
    check("shell scripts", t_shell)
    check("wallpaper PNG", t_wallpaper)
    check("Aurora shell assets", t_aurora_assets)
    check("XOR Shell headless CLI", t_xor_shell_cli)

    ok_all = True
    for ok, name, detail in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}  ({detail})")
        ok_all &= ok
    print("\nOVERALL:", "PASS" if ok_all else "FAIL")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
