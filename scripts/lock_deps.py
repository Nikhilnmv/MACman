#!/usr/bin/env python3
"""Generate a hash-pinned lock for MACman's free tier.

    .venv/bin/python scripts/lock_deps.py > requirements.lock

Every package installed alongside MACman inherits its permissions — up to Full
Disk Access — so the dependency list is security surface, not bookkeeping. A
compromised release of any one of these could read the files MACman can read.
Pinning by hash means an attacker must compromise the *specific artifact* we
recorded, not merely publish a new version.

Two rules this script exists to enforce:

* **Roots come from `pyproject.toml`, never a hand-written list.** An earlier
  hand-maintained copy silently omitted packages, which is worse than no lock:
  it reads as complete.
* **Every digest for a version is kept.** These are multi-wheel projects, one
  per Python version and architecture. Recording a subset produces a lock that
  installs on the machine that generated it and fails everywhere else.

The optional tiers (`cloud`, `voice`, `dev`) are deliberately excluded. They
are opt-in, and the free tier is the install that must be defensible.
"""

from __future__ import annotations

import importlib.metadata as md
import json
import re
import sys
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPI = "https://pypi.org/pypi/{name}/{version}/json"

#: Why each direct dependency is trusted with MACman's permissions. Keyed by
#: normalised name. A dependency with no entry is a dependency nobody justified.
RATIONALE = {
    "pyobjc-core": "Bridge to the macOS frameworks below. Apple-API access only; no network code.",
    "pyobjc-framework-cocoa": "Foundation/AppKit: running apps, workspace queries.",
    "pyobjc-framework-quartz": "Screen capture and display state.",
    "pyobjc-framework-applicationservices": "Accessibility API — lock state, window queries.",
    "pyotp": "RFC 6238 TOTP. ~300 lines, stdlib hmac only, no network, no filesystem.",
    "keyring": "Stores the TOTP secret in the macOS Keychain so it never sits on disk.",
    # Transitive.
    "pyobjc-framework-coretext": "Required by ApplicationServices.",
    # Keys are normalised (PEP 503): dots and underscores become hyphens.
    "jaraco-classes": "keyring dependency.",
    "jaraco-context": "keyring dependency.",
    "jaraco-functools": "keyring dependency.",
    "more-itertools": "jaraco.functools dependency.",
}


def normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def direct_dependencies() -> list[str]:
    """Read the required (non-optional) dependencies from pyproject.toml."""
    with (ROOT / "pyproject.toml").open("rb") as handle:
        data = tomllib.load(handle)
    specs = data["project"]["dependencies"]
    return [re.split(r"[><=!\[;]", spec)[0].strip() for spec in specs]


def closure(roots: list[str]) -> dict[str, str]:
    """Resolve roots to every installed package they pull in, with versions.

    Reads the *installed* environment rather than re-resolving, so the lock
    describes something that demonstrably works.
    """
    resolved: dict[str, str] = {}
    #: (name, had_marker) — a marker may legitimately exclude a package.
    stack: list[tuple[str, bool]] = [(root, False) for root in roots]

    while stack:
        name, conditional = stack.pop()
        key = normalise(name)
        if key in resolved:
            continue
        try:
            resolved[key] = md.version(name)
        except md.PackageNotFoundError:
            # A requirement guarded by a marker — `backports.tarfile;
            # python_version < "3.12"` — is absent because this interpreter
            # does not need it. That is a correct closure, not a gap.
            # An *unguarded* requirement that is missing means the environment
            # is broken, and locking it would record a lie.
            if conditional:
                continue
            print(f"error: {name} is required but not installed; "
                  f"install the free tier first", file=sys.stderr)
            raise SystemExit(1)

        for req in md.requires(name) or []:
            marker = req.split(";", 1)[1] if ";" in req else ""
            # Extras are opt-in and not part of the free tier.
            if "extra" in marker:
                continue
            dep = re.split(r"[><=!\[;(\s]", req.split(";")[0])[0].strip()
            if dep:
                stack.append((dep, bool(marker)))
    return resolved


def digests(name: str, version: str) -> list[str]:
    """Every sha256 PyPI publishes for this version, sdist and wheels alike."""
    with urllib.request.urlopen(PYPI.format(name=name, version=version),
                                timeout=30) as response:
        data = json.load(response)
    return sorted({f["digests"]["sha256"] for f in data["urls"]})


def main() -> int:
    packages = closure(direct_dependencies())

    out = sys.stdout
    out.write("# MACman — free-tier dependency lock\n#\n")
    out.write("# Every package here inherits MACman's permissions, up to Full Disk\n")
    out.write("# Access. This list is security surface. Regenerate with:\n")
    out.write("#   .venv/bin/python scripts/lock_deps.py > requirements.lock\n#\n")
    out.write("# Verified install:\n")
    out.write("#   pip install --require-hashes -r requirements.lock\n#\n")
    out.write(f"# {len(packages)} packages. Optional tiers (cloud, voice, dev) excluded.\n\n")

    missing_rationale = []
    failed = []

    for name, version in sorted(packages.items()):
        why = RATIONALE.get(name)
        if why is None:
            missing_rationale.append(name)
            why = "UNJUSTIFIED — no rationale recorded"
        out.write(f"# {why}\n")
        try:
            hashes = digests(name, version)
        except (urllib.error.URLError, KeyError, json.JSONDecodeError) as exc:
            failed.append(name)
            out.write(f"{name}=={version}  # NO HASHES: {type(exc).__name__}\n\n")
            continue
        out.write(f"{name}=={version} \\\n")
        for i, digest in enumerate(hashes):
            terminator = "" if i == len(hashes) - 1 else " \\"
            out.write(f"    --hash=sha256:{digest}{terminator}\n")
        out.write("\n")

    for name in missing_rationale:
        print(f"warning: {name} has no recorded rationale", file=sys.stderr)
    for name in failed:
        print(f"warning: no hashes retrieved for {name}", file=sys.stderr)

    print(f"locked {len(packages)} packages "
          f"({len(failed)} without hashes, {len(missing_rationale)} unjustified)",
          file=sys.stderr)
    return 1 if failed or missing_rationale else 0


if __name__ == "__main__":
    sys.exit(main())
