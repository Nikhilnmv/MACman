# Setting up and testing MACman

Written for someone who has never seen this project. Follow top to bottom.
Every step says what you should see, so you can tell whether it worked.

**Total time:** about 30 minutes, most of it waiting for Xcode.

---

## Before you start

You need all four:

| | Why | Check |
|---|---|---|
| **macOS 26+** | Apple's on-device model ships with it | `sw_vers -productVersion` |
| **Apple Intelligence on** | The model won't load otherwise |  → Settings → Apple Intelligence & Siri |
| **Full Xcode** | Apple's tool-calling macros ship only with Xcode, not Command Line Tools | `xcode-select -p` |
| **A second Apple device** | iMessage routes to *devices*. You cannot text a Mac from itself | iPhone or iPad on the same Apple ID |

If `xcode-select -p` prints `/Library/Developer/CommandLineTools`, install
Xcode from the App Store, then:

```bash
sudo xcode-select -s /Applications/Xcode.app
sudo xcodebuild -license accept
```

---

## Step 1 — Install

```bash
git clone git@github.com:Nikhilnmv/MACman.git
cd MACman
python3 -m venv .venv
.venv/bin/pip install -e .
```

**Expect:** pip finishes without errors. Takes a minute or two.

---

## Step 2 — Build the Swift helper

This is what talks to Apple's on-device model.

```bash
cd helpers
swift build -c release -Xswiftc -DMACMAN_TOOLS
cd ..
```

**Expect:** `Build complete!`

**If you see** `external macro implementation type 'FoundationModelsMacros...'
could not be found` — you're on Command Line Tools, not full Xcode. Go back to
the prerequisites.

Check the model is reachable:

```bash
./helpers/.build/release/macman-local check
```

**Expect exactly:**

```json
{"available":true,"detail":"ready","tools":true}
```

`"tools":false` means the `-DMACMAN_TOOLS` flag didn't apply — rebuild.
`"available":false` tells you what's wrong (usually Apple Intelligence is off).

---

## Step 3 — Run setup

```bash
.venv/bin/python -m macman.main setup
```

Seven guided steps. It re-checks after each one rather than trusting you.

### 3a. Permissions

macOS will ask you to approve several things. **Grant them to the terminal app
you're running this from** — permissions attach to the calling app, not to
Python. If you later run MACman from a different terminal, you'll be asked
again. That's not a bug.

| Permission | Needed for |
|---|---|
| Accessibility | UI automation, brightness |
| Full Disk Access | reading Messages, so MACman can hear you |
| Screen Recording | screenshots attached to replies |

**Full Disk Access is the one that matters most** — without it MACman cannot
read incoming texts at all.

### 3b. Your handle

The Apple ID or phone number you'll text *from*, in the exact form Messages
stores it — `+919876543210` or `you@icloud.com`.

Not sure? Run this and look at the bottom section:

```bash
.venv/bin/python scripts/verify_imessage.py
```

**Only the top half of that output is safe to share** — it deliberately masks
handles and never prints message content. The bottom lists your real handles,
for your eyes.

### 3c. Wake phrase

Something you'll say to wake it. Default is *"MACman wake up"*. Anything works —
it's matched anywhere in the message, so *"Daddy's home, MACman wake up!"* hits.

### 3d–3f. Engines and credential

Setup checks the on-device model, offers the optional Claude key, and
provisions your login code. **Scan the QR into an authenticator app** (Google
Authenticator, Authy, 1Password — any of them). It won't continue until your
app and your Mac provably agree.

**Expect at the end:** a summary with ✓ against each step.

---

## Step 4 — Test it locally, before involving your phone

```bash
.venv/bin/python -m macman.main run "how many PDF files are in my Downloads folder?"
```

**Expect** a real number, matching:

```bash
ls -1 ~/Downloads/*.pdf | wc -l
```

If those two disagree, stop — something is wrong, and it's worth reporting.

A few more worth trying:

```bash
.venv/bin/python -m macman.main run "what macOS version is this Mac running?"
.venv/bin/python -m macman.main run "how many notes do I have?"
.venv/bin/python -m macman.main run "is Wi-Fi connected?"
```

**First run of each app** (Mail, Calendar, Notes, Reminders, Music, Safari)
raises a one-time Automation dialog. Approve it. MACman gives up after 3
seconds and tells you which app is waiting, so nothing hangs.

---

## Step 5 — The real test, from your phone

```bash
.venv/bin/python -m macman.main serve
```

**Expect:**

```
MACman serving. Allowed: +919876543210
  Mac is unlocked — full access.
  Ctrl-C to stop.
```

Now, from your **other Apple device**, text **your own Apple ID**:

| Send | Expect back |
|---|---|
| `hello` | *(nothing — silence is correct)* |
| `MACman wake up` | `MACman here. Send your code.` |
| the 6-digit code | `MACman ready. Mac is unlocked — full access.` |
| `how many PDF files are in my Downloads folder?` | a real number, with a screenshot |
| `end session` | `Stopped. 1 task this session, $0.000.` |

That first row is not a bug. **An unknown or un-woken sender gets silence** —
replying would confirm the Mac is listening.

---

## Step 6 — Verify the safety behaviour

Worth doing once, so you trust it.

**It asks before anything destructive:**

```bash
.venv/bin/python -m macman.main run "move /tmp/test.txt to the trash"
```
→ asks first. Answer `n`. Nothing is touched. Deleting goes to the **Trash**,
never `rm`.

**It refuses credential paths outright:**

```bash
.venv/bin/python -m macman.main run "read the file at ~/.ssh/id_rsa"
```
→ *"Refused: this references a protected credential path."* This one cannot be
overridden by confirming — it's enforced in code, not by asking the model
nicely.

**Everything is logged:**

```bash
tail -3 ~/Library/Application\ Support/MACman/audit.jsonl
```
→ every tool call, with which engine ran it.

**One command turns it all off:**

```bash
.venv/bin/python scripts/revoke_all.py           # show what would happen
.venv/bin/python scripts/revoke_all.py --revoke  # actually do it
```

---

## Step 7 — Run the test suites

```bash
.venv/bin/python tests/tasks/suite.py --routing
```
**Expect:** `20/20 routed correctly` — confirms private tasks never route to
the cloud.

```bash
.venv/bin/python tests/tasks/suite.py --run local
```
**Expect:** `3/3` — real answers checked against ground truth.

```bash
.venv/bin/python tests/tasks/tool_selection.py --trials 3
```
**Expect:** ~99%. Takes about 8 minutes and executes nothing — every tool call
is stubbed. Fewer than ~90% is worth reporting.

---

## Troubleshooting

**"Permissions are missing" but I granted them**
You granted them to a different app. Permissions attach to the terminal you
run MACman from. Use the same one each time.

**No reply at all when I text**
Check in order: is `serve` running · is your handle in
`~/Library/Application Support/MACman/config.toml` · did you say the wake
phrase first · does `scripts/verify_imessage.py` report `chat.db access: PASS`.

**"Timed out waiting for Automation permission"**
A macOS dialog is waiting on your Mac. Approve it and retry. One-time per app.

**Codes are rejected**
Your authenticator holds a different secret than your Mac. Re-run:
```bash
.venv/bin/python scripts/setup_totp.py --force
```
It won't exit until both provably agree.

**"Claude Code can't authenticate"**
Expected without a key. The on-device half works regardless. To enable it, get
a key from [console.anthropic.com](https://console.anthropic.com) and put it in
`.env` — see `.env.example`.

**Bluetooth says it needs permission**
Grant Bluetooth access to your terminal under System Settings → Privacy &
Security → Bluetooth.

---

## What "working" looks like

You should be able to text your Mac from another room and get correct answers
about your files, control its volume and Wi-Fi, read your unread count, add a
reminder, and open a project in VS Code — **at zero cost, with nothing sent
anywhere**.

If any of that doesn't hold, it's a bug worth reporting, ideally with the
output of:

```bash
.venv/bin/python -m macman.main preflight
```
