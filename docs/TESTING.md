# Setting up MACman, using it, and removing it

Written for someone who has never seen this project. Follow it top to bottom.
Every step says what you should see, so you can tell whether it worked.

**Time:** about 30 minutes, most of it waiting for Xcode to build.

**Where this ends:** MACman running in your menu bar, answering texts from your
phone — and you knowing exactly how to remove every trace of it.

---

## Before you start

| You need | Why |
|---|---|
| **macOS 26** with Apple Intelligence turned on | The free engine is Apple's on-device model. Without it, nothing local works |
| **Full Xcode**, not just Command Line Tools | Apple's tool-calling macros ship only with Xcode. Without them the model runs but cannot *do* anything |
| **A second Apple device** signed into your Apple ID | iMessage routes to *devices*. You cannot text a Mac from itself |
| About 1.5 GB free | Xcode's build cache, then a 70 MB app |

> **There is no release build yet.** You build from source. A Homebrew cask is
> planned but not published, so anyone reading this is installing the way a
> developer does — which is also the audience this is written for.

---

## Step 1 — Get the code and its dependencies

```bash
git clone git@github.com:Nikhilnmv/MACman.git
cd MACman
python3 -m venv .venv && .venv/bin/pip install -e .
```

**Expect:** 11 packages. If it installs 35, you have picked up the optional
Claude SDK — harmless, but the free tier does not need it.

## Step 2 — Build the Swift helpers

```bash
cd helpers && swift build -c release -Xswiftc -DMACMAN_TOOLS && cd ..
```

**Expect:** `Build complete!` after a few minutes.

> **`-DMACMAN_TOOLS` is not optional.** Without it the helper compiles, runs,
> reports itself healthy — and calls no tools at all, so MACman answers from
> the model's memory instead of looking anything up. It goes from 99% correct
> to useless with no error anywhere. If MACman later becomes strangely vague,
> this flag is the first thing to check.

## Step 3 — Build the app

```bash
cd app && ./build.sh --embed && cd ..
```

**Expect:**

```
── Trimmed runtime: 108 MB → 68 MB
── Verifying the bundle stands alone
   34 modules import from the bundled runtime
── Built build/MACman.app
```

`--embed` puts a Python runtime inside the app so it depends on nothing in your
environment. It downloads ~30 MB the first time and verifies it by SHA256.

### Worth doing now: a signing certificate

macOS ties permissions to an app's code signature, and an ad-hoc signature
changes on **every rebuild** — so macOS forgets Full Disk Access each time you
build. That trains you to click through permission dialogs without reading
them, which is a bad habit for this tool in particular.

Free fix, once: **Keychain Access → Certificate Assistant → Create a
Certificate…** → name it `MACman Dev`, Identity Type *Self Signed Root*,
Certificate Type *Code Signing*. `build.sh` finds it automatically and says so.

## Step 4 — First run

```bash
open app/build/MACman.app
```

A 🖥 icon appears in your menu bar. Click it → **Set up MACman…**

The wizard has six steps. Nothing is asked for before it tells you what MACman
cannot do.

| Step | What happens |
|---|---|
| **Welcome** | Three limits, stated before any permission is requested |
| **Permissions** | Six capabilities with live status dots. Each is optional — skipping shows what it disables, not a warning |
| **Who can reach me** | Your phone number in international form, `+447700900123` |
| **Your code** | A QR for your authenticator app, then type a live code to prove it works |
| **Engine** | On-device by default. A Claude key is optional and can be added any time |
| **Check it works** | Runs a real task and counts network connections |

**The permission that matters:** Full Disk Access. MACman reads
`~/Library/Messages/chat.db` to receive your texts, and macOS protects that
file. Grant it to **MACman.app** — the app appears by name in System Settings.

> **Do not grant Full Disk Access to Terminal for this.** It would work, but it
> hands the same access to every script you ever run in a shell, forever.
> Granting it to MACman.app gives it to MACman alone, and you can revoke it
> without crippling your Terminal.

**The last step is the one to pay attention to.** It runs a genuine task on the
on-device model and reports the outbound connection count. You want:

```
There are exactly 284 files in the Downloads folder.
0 network connections — nothing left this Mac
```

That is MACman's central claim, checked on your own machine rather than
asserted in a document.

## Step 5 — Confirm it is actually listening

Click the menu bar icon. The first line must say:

```
Listening for texts — on-device
```

**If it says "Not listening", read the reason underneath** — it will name the
cause, usually missing Full Disk Access or an empty allowlist. That line is
deliberately honest: for a while this app said "Running" while nothing was
listening at all.

## Step 6 — The real test, from your phone

From your other Apple device, message **your own Apple ID**:

| Send | Expect |
|---|---|
| `MACman wake up` | `MACman here. Send your code.` |
| your 6-digit code | `MACman ready. Mac is unlocked — full access.` |
| `how many PDF files are in my Downloads folder?` | an exact number — **check it** |
| `end session` | `Stopped. 1 task this session.` |

If that works, MACman works. Everything else is scope.

## Step 7 — Explore everything it can do

**[VERIFY.md](VERIFY.md)** is the systematic checklist: every capability, the
exact words to send, and what a correct answer looks like. Work through it once.

The most valuable thing you can find is a **confident wrong answer** — a real
number that is not the true number. Those are worse than errors, because you
would believe them.

## Step 8 — See what it did

Menu bar → **Settings… → Activity**.

Every task, which engine ran it, and whether anything left the Mac. The common
case reads *"Nothing left this Mac"*, stated rather than implied.

The count at the top — **sent out today** — is the number that matters. It
should be `0` unless you deliberately used Claude.

---

## Turning it off, and removing it completely

### Pause it

Menu bar → **Stop listening**. MACman stays set up and answers nothing.

Quitting the app also stops it: the daemon is a child of the app, which is what
keeps the permissions attached to MACman rather than to Terminal.

### Revoke access, keep the install

Menu bar → **Settings… → Advanced → Turn off access…**

Deletes your login code and any Claude key from the Keychain. MACman stays
installed but can no longer authenticate anyone.

### Remove every trace

```bash
./scripts/uninstall.sh
```

Shows exactly what it would remove and changes nothing. Then:

```bash
./scripts/uninstall.sh --yes
```

That removes:

| | |
|---|---|
| Both Keychain entries | your login code and any Claude key |
| `~/Library/Application Support/MACman` | settings, activity log, session state |
| `~/Pictures/MACMan` | **screenshots MACman attached to replies** |
| The app | from `/Applications`, or the Homebrew cask |

That third one is worth knowing about: replies can carry a screenshot of your
screen, staged there because Messages will not attach files from Application
Support. It is the most personal thing MACman leaves behind and it is not where
anyone would think to look.

Add `--keep-log` to copy the activity log to your Desktop first.

The script needs nothing but macOS — no repository, no virtualenv — so it still
works after you have deleted everything else.

### The part only you can do

**It cannot revoke macOS permissions**, deliberately: a program able to switch
off its own oversight would be exactly the wrong design.

Open **System Settings → Privacy & Security** and remove MACman from any of:
Full Disk Access, Automation, Accessibility, Screen Recording, Microphone,
Speech Recognition.

If you granted Full Disk Access to **Terminal** at any point while testing,
consider revoking that too. It covers every script you will ever run there.

---

## Troubleshooting

**Menu bar says "Not listening"**
Read the reason on the line below it. Almost always Full Disk Access not
granted to MACman.app, or nobody on the allowlist.

**MACman answers, but vaguely — it never seems to actually look**
The helpers were built without `-DMACMAN_TOOLS`. Rebuild step 2. Settings →
Permissions will also warn that the model cannot use tools.

**Permissions keep being forgotten after every rebuild**
Ad-hoc signing. Create the `MACman Dev` certificate in step 3.

**"unable to open database file"**
Full Disk Access. The message names the pane to open.

**Texts arrive but nothing happens**
Check the handle in Settings matches exactly what Messages shows, including the
country code. Messages from anyone not on the allowlist are dropped in silence
— by design, since any reply would confirm to a stranger that your Mac listens.

**The on-device model is occasionally unavailable**
`LocalEngineUnavailable` happens: Apple's model is a shared system resource and
can decline under memory pressure. MACman reports it rather than guessing an
answer. Try again.

---

## What "working" looks like

- Menu bar: **Listening for texts — on-device**
- A text from your phone gets a correct answer in a few seconds
- **sent out today: 0**
- `~/.ssh/id_rsa` is refused outright, not merely confirmed
- A message from a number not on your allowlist gets **silence**

If all five hold, MACman is doing what it claims.
