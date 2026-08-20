#!/bin/bash
# Remove MACman from this Mac, completely.
#
#   ./uninstall.sh          show exactly what would be removed, change nothing
#   ./uninstall.sh --yes    actually remove it
#   ./uninstall.sh --yes --keep-log     remove everything except the audit log
#
# ## Why this is a shell script
#
# The Python uninstaller needs the repository and its virtualenv. Someone who
# installed a release has neither, and someone who has already dragged
# MACman.app to the Trash has less than that — yet their Keychain entries,
# their config and their screenshots are all still on disk.
#
# An uninstaller that only works from a development checkout is not an
# uninstaller. This needs nothing but macOS, and can be downloaded and run on
# its own.
#
# ## What it cannot do
#
# **It cannot revoke macOS permissions.** Full Disk Access, Automation and the
# rest are revocable only by you, in System Settings. That is not a limitation
# to route around: a program able to switch off its own oversight would be
# exactly the wrong design. They are listed at the end with the pane to open.

set -uo pipefail

DRY_RUN=1
KEEP_LOG=0
for argument in "$@"; do
    case "$argument" in
        --yes)      DRY_RUN=0 ;;
        --keep-log) KEEP_LOG=1 ;;
        -h|--help)  sed -n '2,26p' "$0"; exit 0 ;;
        *) echo "unknown option: $argument" >&2; exit 64 ;;
    esac
done

STATE_DIR="$HOME/Library/Application Support/MACman"
ATTACHMENTS="$HOME/Pictures/MACMan"
LAUNCH_AGENT="$HOME/Library/LaunchAgents/com.macman.agent.plist"
AUDIT_LOG="$STATE_DIR/audit.jsonl"

removed=0
kept=0

say()  { printf '  [%s] %s\n' "$1" "$2"; }
gone() { removed=$((removed + 1)); say "done" "$1"; }
plan() { kept=$((kept + 1));       say "    " "$1"; }

# Refuses to delete anything whose path is empty or unexpectedly short — a
# guard against an unset variable turning `rm -rf "$X"/` into `rm -rf /`.
remove_path() {
    local path="$1" description="$2"
    [ -e "$path" ] || { say "done" "already gone: $description"; return; }

    local size; size="$(du -sh "$path" 2>/dev/null | cut -f1)"
    if [ "$DRY_RUN" = 1 ]; then
        plan "would remove $description — $path ($size)"
        return
    fi
    if [ ${#path} -lt 12 ]; then
        say "SKIP" "refusing to delete a suspiciously short path: $path"
        return
    fi
    rm -rf "$path" && gone "removed $description ($size)"
}

remove_keychain() {
    local service="$1" description="$2"
    if ! security find-generic-password -s "$service" >/dev/null 2>&1; then
        say "done" "$description is not in the Keychain"
        return
    fi
    if [ "$DRY_RUN" = 1 ]; then
        plan "would delete $description from the Keychain ($service)"
        return
    fi
    security delete-generic-password -s "$service" >/dev/null 2>&1 \
        && gone "deleted $description — any code or key it held is now dead"
}

echo
if [ "$DRY_RUN" = 1 ]; then
    echo "MACman uninstall — DRY RUN. Nothing will be changed."
    echo "Re-run with --yes to actually remove."
else
    echo "MACman uninstall — removing."
fi
echo

# --- 1. Stop it -------------------------------------------------------------
echo "Running processes"
pids="$(pgrep -f 'MACman.app|macman.main' 2>/dev/null | tr '\n' ' ')"
if [ -z "${pids// /}" ]; then
    say "done" "nothing running"
elif [ "$DRY_RUN" = 1 ]; then
    plan "would stop $(echo "$pids" | wc -w | tr -d ' ') process(es): $pids"
else
    pkill -f 'MACman.app' 2>/dev/null
    pkill -f 'macman.main' 2>/dev/null
    sleep 1
    gone "stopped MACman"
fi
echo

# --- 2. Credentials ---------------------------------------------------------
# First, because they are what actually grant access. If the rest of this
# script failed halfway, the credentials being gone is what matters.
echo "Credentials"
remove_keychain "com.macman.totp"  "the login code secret"
remove_keychain "com.macman.cloud" "the Claude API key"
echo

# --- 3. Data ----------------------------------------------------------------
echo "Data"
if [ "$KEEP_LOG" = 1 ] && [ -f "$AUDIT_LOG" ]; then
    # Preserve the record of what MACman did, even while removing it. Keeping
    # it is a reasonable thing to want; losing it silently is not.
    if [ "$DRY_RUN" = 1 ]; then
        plan "would keep the audit log at $AUDIT_LOG"
    else
        keep="$HOME/Desktop/macman-audit-$(date +%Y%m%d).jsonl"
        cp "$AUDIT_LOG" "$keep" && say "done" "audit log copied to $keep"
    fi
fi
remove_path "$STATE_DIR"   "settings, audit log and session state"
# Screenshots of your screen, staged here because Messages will not attach
# from Application Support. Worth calling out: this is the most personal thing
# MACman leaves behind, and it is not where anyone would think to look.
remove_path "$ATTACHMENTS" "screenshots MACman sent as attachments"
remove_path "$LAUNCH_AGENT" "a LaunchAgent from an older version"
echo

# --- 4. The app -------------------------------------------------------------
echo "Application"
found_app=0

# Where it is installed, plus wherever a running copy actually lives. The
# second matters for anyone running a build from a checkout: guessing only at
# /Applications would leave the app they are actually using untouched, and
# report success.
# Strip the pid, then everything from /Contents onwards, leaving the bundle
# path. An earlier version matched greedily and captured a *relative*
# "MACman.app", so the directory test failed and a running app was reported as
# not installed — the uninstaller claiming success while leaving it in place.
running_app="$(pgrep -lf 'MACman\.app/Contents/MacOS/MACman' 2>/dev/null \
    | head -1 | sed -E 's|^[0-9]+ ||; s|/Contents/MacOS/MACman.*||')"

for candidate in "/Applications/MACman.app" "$HOME/Applications/MACman.app" "$running_app"; do
    [ -n "$candidate" ] && [ -d "$candidate" ] || continue
    case "$candidate" in
        */app/build/MACman.app)
            # A build artefact inside a source checkout. Reported, never
            # deleted: removing part of someone's working tree is not an
            # uninstaller's job, and `git status` would not explain it.
            say "    " "a development build is at $candidate — delete it yourself if you want it gone"
            found_app=1
            continue ;;
    esac
    found_app=1
    remove_path "$candidate" "MACman.app"
done
[ "$found_app" = 0 ] && say "done" "MACman.app is not installed"

if command -v brew >/dev/null 2>&1 && brew list --cask 2>/dev/null | grep -q '^macman$'; then
    if [ "$DRY_RUN" = 1 ]; then
        plan "would run: brew uninstall --cask macman"
    else
        brew uninstall --cask macman >/dev/null 2>&1 && gone "uninstalled the Homebrew cask"
    fi
fi
echo

# --- 5. What only you can do ------------------------------------------------
cat <<'PERMISSIONS'
macOS permissions — only you can revoke these
─────────────────────────────────────────────
No program should be able to switch off its own oversight, so this script
does not try. Open System Settings → Privacy & Security and remove MACman
from any of these it appears in:

  • Full Disk Access        • Automation
  • Accessibility           • Screen Recording
  • Microphone              • Speech Recognition

  open 'x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles'

If you granted Full Disk Access to Terminal while running MACman from a
checkout, consider revoking that too — it covers every script you ever run
there, not just this one.
PERMISSIONS
echo

if [ "$DRY_RUN" = 1 ]; then
    echo "Nothing was changed. $kept item(s) would be removed — re-run with --yes."
else
    echo "Removed $removed item(s). MACman keeps nothing off this Mac:"
    echo "no account, no server, nothing to cancel."
fi
