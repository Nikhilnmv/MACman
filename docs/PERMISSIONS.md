# Permissions — what was granted, and how to take it back

Written because building MACman involved granting real access to real apps on a
real Mac, and there was no honest account of what ended up where.

**The short version:** macOS gives permissions to the app that *launches* a
program, not to the program. So a permission you thought you were giving MACman
may have gone to Terminal, or to whatever ran the command — and it covers
everything that app ever launches.

---

## Check what is in effect

```bash
.venv/bin/python scripts/permissions.py
```

Run it **from different places**, because it reports different things:

| Run it from | It tells you |
|---|---|
| Terminal | what **Terminal** can do |
| MACman.app's daemon | what **MACman** can do |
| an editor's built-in terminal | what **that editor** can do |

It names the responsible app at the top. That is the app holding the grants.

Two things it deliberately does not do. It cannot read `TCC.db` — macOS
protects it, since a program able to enumerate its own oversight is halfway to
disabling it — so every result is a **behavioural probe**: it tries the thing
and reports what happened. And it cannot revoke anything; it prints the
commands for you to run.

---

## The rule that explains everything

macOS attributes a permission to the **responsible process**: the app that
launched the one asking.

```
You run a script in Terminal
        ↓
The script asks to read a protected file
        ↓
macOS asks: "Allow Terminal to access…?"      ← Terminal, not the script
        ↓
You click Allow
        ↓
EVERY script you ever run in Terminal now has that access. Forever.
```

That is why `MACman.app` exists. Launched by the app, the daemon's permissions
belong to `com.nikhilnmv.macman` — one auditable app you can revoke on its own.

**If you granted Full Disk Access to Terminal while testing MACman, that grant
is still there and still applies to everything else you run in a shell.** It is
the single most worthwhile thing to revoke on this page.

---

## What MACman actually needs

| Permission | Needed? | What it is for | Without it |
|---|---|---|---|
| **Full Disk Access** | **Required** for texting | Reading `~/Library/Messages/chat.db` | No text channel. Everything else works |
| **Automation** | **Required** for app control | Mail, Calendar, Notes, Reminders, browsers, Pages | Those apps cannot be asked anything |
| Accessibility | Optional | Screen brightness, lock-state detail | Brightness cannot change |
| Screen Recording | Optional | Screenshots attached to replies | Replies are text only |
| Microphone | Optional | Voice mode | No voice |
| Speech Recognition | Optional | Voice mode | No voice |

**Only the first two are load-bearing.** If you are not using voice or
screenshots, the last four can go and MACman still does its main job.

Microphone and Speech Recognition are **two separate grants** that macOS tracks
independently, and they diverge in practice — a host can hold the microphone
while speech recognition is still undetermined. `scripts/permissions.py` shows
them separately for that reason.

---

## What Claude may hold, and why

Claude Code ran throughout this project's development, and anything it launches
inherits its permissions. On this Mac, at the time of writing, running the
checker under Claude Code reported:

| Permission | State |
|---|---|
| Automation | **granted** |
| Screen Recording | **granted** |
| Microphone | **granted** |
| Speech Recognition | not determined |
| Full Disk Access | not granted |
| Accessibility | not granted |

Those were granted so MACman's tools could be tested — driving Mail, capturing
screenshots, checking the speech pipeline. **None of them are needed once
MACman runs from its own app**, which now holds its own grants.

There is a wrinkle worth knowing: Claude Code reports the bundle identifier
`com.anthropic.claude-code`, while the desktop app is
`com.anthropic.claudefordesktop`. They are different entries in System
Settings, so resetting one does not touch the other. Check both.

---

## Revoking

### The clear way: System Settings

```bash
open 'x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles'
```

Go through each pane — Full Disk Access, Automation, Accessibility, Screen
Recording, Microphone, Speech Recognition — and remove anything you do not want
there. This is the only view that shows every app at once, which is why it is
the recommended route.

### The precise way: `tccutil`

```bash
tccutil reset All com.apple.Terminal                 # every script you run in a shell
tccutil reset All com.anthropic.claude-code          # Claude Code
tccutil reset All com.anthropic.claudefordesktop     # the Claude desktop app
tccutil reset All com.microsoft.VSCode               # its terminal and extensions
```

`reset All` clears every permission for that bundle. Nothing is destroyed — the
app will ask again the next time it genuinely needs something, which is the
point: you get to answer the question again, knowingly.

To reset a single permission instead:

```bash
tccutil reset SystemPolicyAllFiles com.apple.Terminal   # Full Disk Access only
tccutil reset AppleEvents com.apple.Terminal            # Automation only
tccutil reset ScreenCapture com.apple.Terminal
tccutil reset Microphone com.apple.Terminal
tccutil reset SpeechRecognition com.apple.Terminal
tccutil reset Accessibility com.apple.Terminal
```

### MACman itself

```bash
tccutil reset All com.nikhilnmv.macman
```

Safe to run any time. MACman asks again through the setup wizard, and until
then the menu bar says **Not listening** with the reason — it will not pretend
to work.

---

## A suggested order

If the goal is "leave my Mac the way I found it, minus MACman":

1. **Remove MACman** — `./scripts/uninstall.sh --yes`
   *(see [TESTING.md](TESTING.md#turning-it-off-and-removing-it-completely))*
2. **Reset MACman's permissions** — `tccutil reset All com.nikhilnmv.macman`
3. **Reset Terminal's** — this is the broad one, and the one most worth doing
4. **Reset Claude's, both identifiers** — if you are done with the development
   work that needed them
5. **Check** — `.venv/bin/python scripts/permissions.py` from Terminal should
   now report everything as not granted

Step 3 is the one that matters most. The others are tidiness; that one narrows
access that currently applies to every command you type.

---

## What this cannot tell you

**Which app holds a permission you have not run a check from.** The probe
reports the process that launched it, so learning what some other app holds
means running it from there — or reading System Settings, which is why that
route is recommended first.

**Whether a permission was ever used.** macOS records that access was granted,
not what was done with it. MACman's own `audit.jsonl` records every tool call
it made, but no such record exists for anything else on your Mac.

**Anything about permissions outside TCC** — network access, keychain items
belonging to other apps, browser extensions. This page is about the six macOS
privacy permissions MACman touches, and nothing wider.
