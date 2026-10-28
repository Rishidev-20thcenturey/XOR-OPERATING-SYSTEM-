#!/usr/bin/env python3
"""Check the XOR package list against the configured Debian Trixie indices.

The ISO build must download packages anyway, but a long ``lb build`` should
not be the first place a typo such as ``fonts-Inter`` is discovered.  This
tool fetches ``Packages.xz`` from the base, updates and security suites for
the archive areas configured in ``os/live-build/auto/config``, then verifies:

  * every requested package exists with that exact (lowercase) name;
  * every Pre-Depends/Depends alternative has a real package or virtual
    provider in the indices, recursively through dependencies.

It is deliberately a name-level dependency closure, not a replacement for
apt's version, conflict and architecture resolver.  apt/lb remains the
authoritative resolver; this catches missing names and broken dependency
graphs before an expensive image build starts.

Usage:
    python3 tools/check-packages.py
    python3 tools/check-packages.py --package-list path/to/list
    python3 tools/check-packages.py --offline-index-dir /path/to/indexes

Exit status is non-zero if the list cannot be fetched or contains a missing
package / unresolved dependency.
"""

from __future__ import annotations

import argparse
import lzma
import os
import re
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

REPO = Path(__file__).resolve().parent.parent
DEFAULT_LIST = REPO / "os/live-build/config/package-lists/xor.list.chroot"
DEFAULT_MIRROR = os.environ.get("XOR_DEBIAN_MIRROR", "https://deb.debian.org/debian")
DEFAULT_SECURITY_MIRROR = os.environ.get(
    "XOR_DEBIAN_SECURITY_MIRROR", "https://security.debian.org/debian-security")
DEFAULT_SUITES = ("trixie", "trixie-updates", "trixie-security")
DEFAULT_AREAS = ("main", "contrib", "non-free-firmware")
INDEX_PATH = "dists/{suite}/{area}/{arch}/Packages.xz"


class CheckError(Exception):
    """A fetch or format problem that prevents a trustworthy answer."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-list", type=Path, default=DEFAULT_LIST,
                        help="package list to check (default: %(default)s)")
    parser.add_argument("--mirror", default=DEFAULT_MIRROR,
                        help="Debian archive mirror (default: %(default)s)")
    parser.add_argument("--security-mirror", default=DEFAULT_SECURITY_MIRROR,
                        help="Debian security mirror (default: %(default)s)")
    parser.add_argument("--suite", action="append", dest="suites",
                        help="suite to fetch; may be repeated (default: trixie, "
                             "trixie-updates, trixie-security)")
    parser.add_argument("--area", action="append", dest="areas",
                        help="archive area to fetch; may be repeated "
                             "(default: main, contrib, non-free-firmware)")
    parser.add_argument("--arch", default="binary-amd64",
                        help="Debian binary architecture path (default: %(default)s)")
    parser.add_argument("--timeout", type=float, default=90.0,
                        help="network timeout per index in seconds")
    parser.add_argument("--offline-index-dir", type=Path,
                        help="use previously downloaded Packages.xz files in "
                             "this directory instead of the network")
    return parser.parse_args()


def index_url(mirror: str, suite: str, area: str, arch: str) -> str:
    return f"{mirror.rstrip('/')}/{INDEX_PATH.format(suite=suite, area=area, arch=arch)}"


def fetch_index(mirror: str, suite: str, area: str, arch: str,
                timeout: float, offline_dir: Path | None) -> bytes:
    filename = f"{suite}-{area}-{arch.replace('/', '-')}-Packages.xz"
    if offline_dir is not None:
        path = offline_dir / filename
        try:
            return path.read_bytes()
        except OSError as exc:
            raise CheckError(f"cannot read offline index {path}: {exc}") from exc

    url = index_url(mirror, suite, area, arch)
    request = Request(url, headers={"User-Agent": "xor-package-check/1.0"})
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read()
    except HTTPError as exc:
        raise CheckError(f"HTTP {exc.code} fetching {url}") from exc
    except URLError as exc:
        raise CheckError(f"cannot fetch {url}: {exc.reason}") from exc
    except OSError as exc:
        raise CheckError(f"cannot fetch {url}: {exc}") from exc


def parse_packages(data: bytes, source: str) -> list[dict[str, str]]:
    try:
        text = lzma.decompress(data).decode("utf-8", "replace")
    except lzma.LZMAError as exc:
        raise CheckError(f"{source}: not a valid xz-compressed Packages file") from exc

    records: list[dict[str, str]] = []
    current: dict[str, str] = {}
    last_key: str | None = None
    for line in text.splitlines():
        if not line:
            if "Package" in current:
                records.append(current)
            current = {}
            last_key = None
            continue
        if line[0] in " \t":
            if last_key is not None:
                current[last_key] += "\n" + line.strip()
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        last_key = key
        current[key] = value.strip()
    if "Package" in current:
        records.append(current)
    return records


def parse_list(path: Path) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise CheckError(f"cannot read package list {path}: {exc}") from exc
    packages = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        packages.append(line)
    return packages


def split_dependencies(field: str) -> list[str]:
    """Return dependency clauses, ignoring architecture/build restrictions."""
    clauses = []
    for clause in field.split(","):
        clause = re.sub(r"\[[^\]]*\]", "", clause)
        clause = re.sub(r"<[^>]*>", "", clause).strip()
        if clause:
            clauses.append(clause)
    return clauses


def alternative_names(clause: str) -> list[str]:
    names = []
    for alternative in clause.split("|"):
        match = re.match(
            r"^([a-z0-9][a-z0-9+.-]*)(?::[a-z0-9-]+)?(?:\s*\([^)]*\))?\s*$",
            alternative.strip(),
        )
        if match:
            names.append(match.group(1))
    return names


def add_providers(record: dict[str, str], name: str,
                  providers: dict[str, set[str]]) -> None:
    for item in record.get("Provides", "").split(","):
        item = item.strip()
        if not item:
            continue
        virtual = item.split()[0].split("=")[0].strip()
        if virtual:
            providers.setdefault(virtual, set()).add(name)


def load_indices(mirror: str, security_mirror: str, suites: list[str],
                 areas: list[str], arch: str, timeout: float,
                 offline_dir: Path | None) -> tuple[dict[str, dict[str, str]],
                                                   dict[str, set[str]], int]:
    packages: dict[str, dict[str, str]] = {}
    providers: dict[str, set[str]] = {}
    records_read = 0
    for suite in suites:
        suite_mirror = security_mirror if suite.endswith("-security") else mirror
        for area in areas:
            source = (f"offline {suite}/{area}" if offline_dir is not None
                      else index_url(suite_mirror, suite, area, arch))
            data = fetch_index(suite_mirror, suite, area, arch, timeout, offline_dir)
            records = parse_packages(data, source)
            records_read += len(records)
            print(f"  {suite}/{area}: {len(records)} package records")
            for record in records:
                name = record.get("Package", "")
                if not name:
                    continue
                # Later suites (updates/security) replace the base stanza.
                packages[name] = record
    # Rebuild the virtual-provider map from the final (newest) stanzas.
    providers = {}
    for name, record in packages.items():
        add_providers(record, name, providers)
    return packages, providers, records_read


def choose_alternative(clause: str, packages: dict[str, dict[str, str]],
                       providers: dict[str, set[str]]) -> str | None:
    for name in alternative_names(clause):
        if name in packages:
            return name
        if name in providers:
            return sorted(providers[name])[0]
    return None


def check_dependencies(requested: list[str], packages: dict[str, dict[str, str]],
                       providers: dict[str, set[str]]) -> list[tuple[str, str]]:
    unresolved: list[tuple[str, str]] = []
    seen: set[str] = set()
    queue: list[tuple[str, str | None]] = [(name, None) for name in requested]
    while queue:
        name, parent = queue.pop()
        if name in seen:
            continue
        seen.add(name)
        if name not in packages:
            if parent is None:
                unresolved.append(
                    (name, f"requested package {name!r} is not in the indices"))
            else:
                unresolved.append(
                    (name, f"dependency of {parent!r} cannot be resolved"))
            continue
        record = packages[name]
        for field in ("Pre-Depends", "Depends"):
            for clause in split_dependencies(record.get(field, "")):
                chosen = choose_alternative(clause, packages, providers)
                if chosen is None:
                    unresolved.append((name, clause))
                else:
                    queue.append((chosen, name))
    return unresolved


def main() -> int:
    args = parse_args()
    suites = args.suites or list(DEFAULT_SUITES)
    areas = args.areas or list(DEFAULT_AREAS)
    try:
        requested = parse_list(args.package_list)
        if not requested:
            raise CheckError(f"{args.package_list}: package list is empty")
        print(f"Package list: {args.package_list} ({len(requested)} entries)")
        print("Fetching Debian Trixie indices:")
        packages, providers, records_read = load_indices(
            args.mirror, args.security_mirror, suites, areas, args.arch,
            args.timeout, args.offline_index_dir)
    except CheckError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2

    missing = [name for name in requested if name not in packages]
    if missing:
        print("FAIL: requested package(s) not found in configured repositories:",
              file=sys.stderr)
        for name in missing:
            hint = ""
            if name.lower() in packages:
                hint = f" (Debian names are lowercase; did you mean {name.lower()}?)"
            print(f"  {name}{hint}", file=sys.stderr)
        return 1

    unresolved = check_dependencies(requested, packages, providers)
    if unresolved:
        print("FAIL: unresolved dependency clauses:", file=sys.stderr)
        for package, detail in unresolved[:50]:
            print(f"  {package}: {detail}", file=sys.stderr)
        if len(unresolved) > 50:
            print(f"  ... and {len(unresolved) - 50} more", file=sys.stderr)
        return 1

    print(f"PASS: {len(requested)} requested packages found; "
          f"{len(packages)} package names across {records_read} records; "
          "dependency names resolve.")
    print("Note: apt/lb remains authoritative for versions, conflicts and "
          "architecture selection.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
