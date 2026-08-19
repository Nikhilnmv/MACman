#!/bin/bash
# Assemble MACman.app around the SwiftPM executable.
#
#   ./build.sh              debug build, signed with a stable dev identity
#   ./build.sh --release    release build
#
# Why a script rather than an .xcodeproj: this is readable, diffable and
# reviewable. An Xcode project is a binary-ish blob that hides exactly the
# decisions — entitlements, signing identity, bundle layout — that matter most
# for a tool asking for Full Disk Access.
#
# ## Signing, and why it matters even before paying Apple
#
# TCC remembers a permission against the app's **code signing identity**. An
# ad-hoc signature (`codesign -s -`) derives that from the binary's hash, so it
# changes on every single rebuild — and macOS forgets that you granted Full
# Disk Access, every time. That makes development miserable and, worse, trains
# you to click through permission dialogs without reading them.
#
# A self-signed certificate fixes it for free: the identity stays constant
# across rebuilds, so permissions persist. It does nothing for distribution —
# Gatekeeper still objects on another Mac — but that is a separate decision.
#
# Create one once:
#
#   Keychain Access → Certificate Assistant → Create a Certificate…
#     Name: MACman Dev
#     Identity Type: Self Signed Root
#     Certificate Type: Code Signing
#
# Then this script picks it up automatically.

set -euo pipefail

cd "$(dirname "$0")"

CONFIGURATION="debug"
[[ "${1:-}" == "--release" ]] && CONFIGURATION="release"

APP_NAME="MACman"
BUNDLE="build/${APP_NAME}.app"
DEV_IDENTITY="MACman Dev"

echo "── Building ${APP_NAME} (${CONFIGURATION})"
swift build -c "${CONFIGURATION}" 2>&1 | grep -E "error|warning:|Compiling|Build complete" || true

BINARY="$(swift build -c "${CONFIGURATION}" --show-bin-path)/${APP_NAME}"
if [[ ! -x "${BINARY}" ]]; then
    echo "   build produced no executable at ${BINARY}" >&2
    exit 1
fi

echo "── Assembling ${BUNDLE}"
rm -rf "${BUNDLE}"
mkdir -p "${BUNDLE}/Contents/MacOS" "${BUNDLE}/Contents/Resources"

cp "${BINARY}" "${BUNDLE}/Contents/MacOS/${APP_NAME}"
cp Resources/Info.plist "${BUNDLE}/Contents/Info.plist"
printf 'APPL????' > "${BUNDLE}/Contents/PkgInfo"

# Phase C runs the daemon from the repository's virtualenv, so the bundle
# carries no Python yet. Embedding a relocatable interpreter is phase C's
# second half; DaemonController falls back to the repo until it exists.

echo "── Signing"
if security find-identity -v -p codesigning 2>/dev/null | grep -q "${DEV_IDENTITY}"; then
    codesign --force --deep --sign "${DEV_IDENTITY}" \
             --options runtime "${BUNDLE}" 2>&1 | sed 's/^/   /'
    echo "   signed with '${DEV_IDENTITY}' — permissions persist across rebuilds"
else
    codesign --force --deep --sign - "${BUNDLE}" 2>&1 | sed 's/^/   /'
    cat <<'WARNING'
   signed ad-hoc.

   ⚠ macOS ties permissions to the signing identity, and an ad-hoc signature
     changes on every rebuild — so Full Disk Access will need granting again
     after each build. Create a self-signed "MACman Dev" certificate in
     Keychain Access to avoid that. See the comments at the top of this file.
WARNING
fi

echo
echo "── Built ${BUNDLE}"
echo "   open ${PWD}/${BUNDLE}"
