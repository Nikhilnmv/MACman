#!/bin/bash
# Put a Python runtime, the daemon and the Swift helpers inside MACman.app.
#
#   ./embed_runtime.sh <bundle path>     embed into an assembled bundle
#   ./embed_runtime.sh --pin             print the lines to pin a new runtime
#
# ## Why the app carries its own Python
#
# macOS ships Python 3.9; the daemon needs 3.11 for `tomllib`. Depending on a
# Homebrew Python means the app breaks the day the user runs `brew upgrade`,
# which is a support burden paid by someone who did nothing wrong.
#
# ## Why it is pinned by hash
#
# This downloads a Python interpreter that will run with whatever permissions
# the user grants MACman — up to Full Disk Access. That makes it exactly the
# same class of supply-chain surface as `requirements.lock`, and it gets the
# same treatment: an exact version, an exact hash, and a refusal to proceed on
# a mismatch. "Fetch the latest" would mean the bundle's contents depend on
# what a server felt like serving that day.
#
# To move to a newer runtime, run `./embed_runtime.sh --pin`, review what it
# prints, and paste it in below.

set -euo pipefail
cd "$(dirname "$0")"

# --- The pin -----------------------------------------------------------------
# astral-sh/python-build-standalone, aarch64-apple-darwin, install_only.
RUNTIME_VERSION="20260814"
RUNTIME_URL="https://github.com/astral-sh/python-build-standalone/releases/download/20260814/cpython-3.13.15%2B20260814-aarch64-apple-darwin-install_only.tar.gz"
RUNTIME_SHA256="7d50bb42813a5644db7c40d3ad79361d0b724bb29d25a91fab1048c2c5c6a8c5"
# -----------------------------------------------------------------------------

CACHE=".cache"
REPO_ROOT="$(cd .. && pwd)"

pin() {
    echo "Finding the current python-build-standalone release…"
    local api="https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest"
    local tag asset url sha
    local release="$CACHE/release.json"
    mkdir -p "$CACHE"
    curl -fsSL --retry 3 --retry-delay 2 --max-time 120 "$api" -o "$release"
    tag="$(/usr/bin/python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["tag_name"])' "$release")"

    # Exact match, not "first alphabetically". An earlier version took
    # sorted(...)[0] and silently selected the *freethreaded* (no-GIL) build,
    # because "freethreaded" sorts ahead of the standard one. pyobjc and most
    # other C extensions publish no freethreaded wheels, so the dependency
    # install would have failed — after downloading 30 MB — for a reason
    # nothing in the output would have explained.
    url="$(/usr/bin/python3 - "$release" <<'SELECT'
import json, sys

assets = json.load(open(sys.argv[1]))["assets"]
matches = [
    a["browser_download_url"] for a in assets
    if a["name"].startswith("cpython-3.13.")
    and a["name"].endswith("-aarch64-apple-darwin-install_only.tar.gz")
]
if len(matches) != 1:
    print(f"expected exactly one match, found {len(matches)}", file=sys.stderr)
    for name in matches:
        print(f"  {name}", file=sys.stderr)
    sys.exit(1)
print(matches[0])
SELECT
)"

    if [[ -z "$url" ]]; then
        echo "No matching asset found in $tag." >&2
        exit 1
    fi

    local file="$CACHE/$(basename "$url")"
    echo "Downloading $(basename "$url")…"
    # `-C -` resumes a partial file, because this is 30 MB over a link that
    # has already timed out once here, and a truncated archive that looks
    # downloaded is worse than one that obviously failed.
    curl -fSL --progress-bar --retry 5 --retry-delay 3 -C - "$url" -o "$file"
    sha="$(shasum -a 256 "$file" | cut -d' ' -f1)"

    cat <<PIN

Reviewed and correct? Paste these three lines into embed_runtime.sh:

RUNTIME_VERSION="$tag"
RUNTIME_URL="$url"
RUNTIME_SHA256="$sha"

PIN
}

fetch_runtime() {
    if [[ "$RUNTIME_VERSION" == "UNPINNED" || -z "$RUNTIME_URL" ]]; then
        cat >&2 <<'UNSET'
No Python runtime is pinned yet.

    ./embed_runtime.sh --pin

Review what it prints, then paste the three lines into this script. Nothing is
downloaded into the bundle until a hash is recorded, on purpose: this
interpreter runs with MACman's permissions.
UNSET
        exit 2
    fi

    mkdir -p "$CACHE"
    local file="$CACHE/$(basename "$RUNTIME_URL")"

    if [[ ! -f "$file" ]]; then
        echo "── Downloading Python runtime $RUNTIME_VERSION"
        curl -fSL --progress-bar --retry 5 --retry-delay 3 -C - \
             "$RUNTIME_URL" -o "$file"
    fi

    echo "── Verifying runtime hash"
    local actual
    actual="$(shasum -a 256 "$file" | cut -d' ' -f1)"
    if [[ "$actual" != "$RUNTIME_SHA256" ]]; then
        echo "   HASH MISMATCH — refusing to embed." >&2
        echo "   expected $RUNTIME_SHA256" >&2
        echo "   got      $actual" >&2
        rm -f "$file"
        exit 1
    fi
    echo "   ok"
    RUNTIME_ARCHIVE="$file"
}

# Strip what a headless daemon can never reach.
#
# Runs *after* the dependency install, since pip is one of the things removed.
# Everything here is either a test suite, a build-time tool, or a GUI toolkit —
# no runtime import path leads to any of it. Measured on the first embed:
# 110 MB before, and the two largest items were surprises rather than the
# obvious ones. PyObjCTest is pyobjc's own test suite, shipped inside the
# wheel; pip is 11 MB the app has no use for once the bundle is built.
#
# Deliberately conservative. Trimming the stdlib module-by-module saves a few
# more megabytes and risks a missing import that only shows up on a user's Mac
# months later, which is a bad trade for a tool asking to be trusted.
trim_runtime() {
    local python="$1"
    local before after
    before="$(du -sk "$python" | cut -f1)"

    local -a removable=(
        "lib/python3.13/site-packages/PyObjCTest"   # pyobjc's test suite
        "lib/python3.13/site-packages/pip"          # build-time only
        "lib/python3.13/site-packages/setuptools"
        "lib/python3.13/site-packages/pkg_resources"
        "lib/python3.13/test"                       # CPython's test suite
        "lib/python3.13/idlelib"                    # the IDLE editor
        "lib/python3.13/tkinter"                    # GUI toolkit
        "lib/python3.13/ensurepip"
        "lib/python3.13/pydoc_data"
        "lib/python3.13/lib2to3"
        "include"                                   # C headers
        "share"                                     # man pages
        "lib/tcl9.0" "lib/tcl9" "lib/tk9.0" "lib/itcl4.3.8"
        "lib/tdbc1.1.10" "lib/thread3.0.4" "lib/sqlite3.50.4"
    )

    for path in "${removable[@]}"; do
        rm -rf "${python:?}/${path}"
    done

    # Tcl/Tk shared libraries and any static archives.
    find "$python/lib" -maxdepth 1 \
        \( -name 'libtcl*' -o -name 'libtk*' -o -name '*.a' \) \
        -delete 2>/dev/null || true

    # pip's console scripts now point at nothing.
    find "$python/bin" -maxdepth 1 \
        \( -name 'pip*' -o -name 'idle*' -o -name '2to3*' \) \
        -delete 2>/dev/null || true

    after="$(du -sk "$python" | cut -f1)"
    echo "── Trimmed runtime: $((before / 1024)) MB → $((after / 1024)) MB"
}

# Prove the bundle is self-contained before calling it built.
#
# The trim list above is hand-written, and the failure mode of getting it wrong
# is the worst kind: the app launches, the menu bar looks healthy, and one
# feature raises ImportError on a user's Mac weeks later. So every module the
# daemon can reach is imported here, from the bundled interpreter, with the
# repository's own site-packages deliberately out of the picture.
verify() {
    local resources="$1"
    echo "── Verifying the bundle stands alone"

    env -i PATH=/usr/bin:/bin \
        PYTHONPATH="$resources/daemon" \
        MACMAN_HELPERS_BIN="$resources/helpers" \
        "$resources/python/bin/python3" - <<'CHECK'
import importlib, sys

modules = [
    "macman.config", "macman.router", "macman.session", "macman.appbridge",
    "macman.security.egress", "macman.security.auth", "macman.security.paths",
    "macman.security.audit", "macman.security.lockstate",
    "macman.security.permissions", "macman.engines.local",
    "macman.agent.tools.actions", "macman.agent.tools.typed",
    "macman.agent.tools.registry", "macman.agent.guard",
    "macman.channels.imessage", "macman.channels.confirm",
    "macman.voice.digits", "macman.voice.speech", "macman.voice.session",
    "macman.preflight", "macman.setup", "macman.userconfig",
    # Third-party and stdlib the daemon depends on at runtime.
    "keyring", "pyotp", "AppKit", "Quartz", "ApplicationServices",
    "sqlite3", "tomllib", "ssl", "hashlib", "select", "json",
]

failed = []
for name in modules:
    try:
        importlib.import_module(name)
    except Exception as exc:                      # noqa: BLE001
        failed.append(f"{name}: {type(exc).__name__}: {exc}")

if failed:
    print(f"   {len(failed)} module(s) missing from the bundle:")
    for line in failed:
        print(f"     {line}")
    sys.exit(1)
print(f"   {len(modules)} modules import from the bundled runtime")
CHECK
}

embed() {
    local bundle="$1"
    [[ -d "$bundle" ]] || { echo "No bundle at $bundle" >&2; exit 1; }
    local resources="$bundle/Contents/Resources"

    fetch_runtime

    echo "── Extracting runtime"
    rm -rf "$resources/python"
    mkdir -p "$resources/python"
    # The archive contains a top-level `python/` directory.
    tar -xzf "$RUNTIME_ARCHIVE" -C "$resources" python

    local python="$resources/python/bin/python3"
    [[ -x "$python" ]] || { echo "   runtime has no bin/python3" >&2; exit 1; }

    echo "── Installing locked dependencies"
    # The same lock the audits check, so what ships matches what was audited.
    "$python" -m pip install --quiet --disable-pip-version-check \
        --require-hashes -r "$REPO_ROOT/requirements.lock" 2>&1 | sed 's/^/   /'

    trim_runtime "$resources/python"

    echo "── Copying the daemon"
    rm -rf "$resources/daemon"
    mkdir -p "$resources/daemon"
    # Source only: no __pycache__, no .venv, nothing from the working tree that
    # is not part of the program.
    rsync -a --exclude='__pycache__' --exclude='*.pyc' \
        "$REPO_ROOT/macman" "$resources/daemon/"

    echo "── Copying the Swift helpers"
    rm -rf "$resources/helpers"
    mkdir -p "$resources/helpers"
    local built="$REPO_ROOT/helpers/.build/release"
    if [[ ! -d "$built" ]]; then
        echo "   helpers not built — run:" >&2
        echo "     cd helpers && swift build -c release -Xswiftc -DMACMAN_TOOLS" >&2
        exit 1
    fi
    for helper in macman-local macman-speech macman-audio macman-state macman-ax; do
        [[ -f "$built/$helper" ]] && cp "$built/$helper" "$resources/helpers/"
    done

    verify "$resources"

    echo "── Embedded:"
    echo "   python   $(du -sh "$resources/python" | cut -f1)"
    echo "   daemon   $(du -sh "$resources/daemon" | cut -f1)"
    echo "   helpers  $(du -sh "$resources/helpers" | cut -f1)"
    echo "   bundle   $(du -sh "$bundle" | cut -f1)"
}

case "${1:-}" in
    --pin) pin ;;
    "")    echo "usage: $0 <bundle path> | --pin" >&2; exit 64 ;;
    *)     embed "$1" ;;
esac
