#!/bin/bash
# Build a release artifact and print everything needed to publish it.
#
#   ./scripts/release.sh
#
# Produces `dist/MACman-<version>.zip` and the exact cask stanza to paste, with
# the real SHA256. It does not tag, push, upload or publish anything — those
# are irreversible and public, so they stay in your hands.
#
# ## The signing question, which this cannot answer for you
#
# `brew install --cask` applies `com.apple.quarantine` to what it downloads, so
# an **unsigned** app installed this way shows "unidentified developer" and
# refuses to open until the user right-clicks it or passes `--no-quarantine`.
#
# Telling a security-conscious person to bypass Gatekeeper for a tool that
# wants Full Disk Access undercuts the entire pitch. The options are honest but
# none is free:
#
#   1. Sign and notarize        $99/yr, clean install, no warning
#   2. Ship unsigned            free, and the install guide must explain the
#                               warning and how to get past it
#   3. Homebrew *formula*       free and unquarantined, but the user needs
#      (build from source)      Xcode and waits for a build
#
# This script builds the artifact either way and reports which signature the
# bundle carries, so the decision is made with the evidence in front of you.

set -euo pipefail
cd "$(dirname "$0")/.."

VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' macman/__init__.py)"
[[ -n "${VERSION}" ]] || { echo "no __version__ in macman/__init__.py" >&2; exit 1; }

APP="app/build/MACman.app"
DIST="dist"
ARCHIVE="${DIST}/MACman-${VERSION}.zip"

echo "── Building MACman ${VERSION}"

# Always a fresh build with the runtime embedded: a release must not depend on
# whatever happened to be in app/build from the last debug run.
( cd app && ./build.sh --release --embed ) 2>&1 | sed 's/^/   /' | grep -E \
    "Version|Trimmed|modules import|signed|Built|error" || true

[[ -d "${APP}" ]] || { echo "no app at ${APP}" >&2; exit 1; }

echo
echo "── Checking what you are about to ship"

SIGNATURE="$(codesign -dvvv "${APP}" 2>&1 | sed -n 's/^Authority=//p' | head -1)"
if [[ -z "${SIGNATURE}" ]]; then
    SIGNATURE="$(codesign -dvvv "${APP}" 2>&1 | sed -n 's/^Signature=//p' | head -1)"
fi
echo "   signature: ${SIGNATURE:-none}"

case "${SIGNATURE}" in
    *"Developer ID"*)
        echo "   ✓ Developer ID — installs cleanly, no Gatekeeper warning."
        QUARANTINE_NOTE="" ;;
    *)
        cat <<'UNSIGNED'
   ⚠ Not signed with a Developer ID.

     `brew install --cask` quarantines what it downloads, so users will see
     "MACman.app cannot be opened because the developer cannot be verified"
     and it will refuse to open. They would need:

         brew install --no-quarantine nikhilnmv/tap/macman

     or a right-click → Open the first time. Both are reasonable to ask of a
     developer audience, and both are a bad first impression for a tool that
     then asks for Full Disk Access. Decide deliberately, and if you ship this
     way, put the workaround in the install instructions rather than letting
     people discover it.
UNSIGNED
        QUARANTINE_NOTE="  # unsigned: users need --no-quarantine" ;;
esac

echo
echo "── Packaging"
rm -rf "${DIST}" && mkdir -p "${DIST}"
# ditto rather than zip: it preserves the bundle's symlinks, resource forks and
# — critically — its code signature, which a plain `zip` can quietly break.
ditto -c -k --sequesterRsrc --keepParent "${APP}" "${ARCHIVE}"

SHA="$(shasum -a 256 "${ARCHIVE}" | cut -d' ' -f1)"
SIZE="$(du -h "${ARCHIVE}" | cut -f1)"
echo "   ${ARCHIVE}  (${SIZE})"
echo "   sha256 ${SHA}"

echo
echo "── Verifying the archive is intact"
rm -rf "${DIST}/verify" && mkdir -p "${DIST}/verify"
ditto -x -k "${ARCHIVE}" "${DIST}/verify"
if codesign --verify --deep --strict "${DIST}/verify/MACman.app" 2>/dev/null; then
    echo "   ✓ signature survives the round trip"
else
    echo "   ⚠ signature does not verify after unzip — investigate before publishing"
fi
"${DIST}/verify/MACman.app/Contents/Resources/python/bin/python3" -c \
    "import sys; sys.path.insert(0, '${PWD}/${DIST}/verify/MACman.app/Contents/Resources/daemon'); \
     import macman; print('   ✓ unpacked app reports version', macman.__version__)"
rm -rf "${DIST}/verify"

cat <<NEXT

── Next steps, for you to run

  1. Tag and push:

       git tag -a v${VERSION} -m "MACman ${VERSION}"
       git push origin v${VERSION}

  2. Create the release and attach the archive:

       gh release create v${VERSION} "${ARCHIVE}" \\
         --title "MACman ${VERSION}" --notes-file docs/CHANGELOG.md

  3. Update the cask in Nikhilnmv/homebrew-tap with:

       version "${VERSION}"
       sha256 "${SHA}"${QUARANTINE_NOTE}

  Nothing above has been done for you. Tagging and publishing are public and
  hard to undo.

NEXT
