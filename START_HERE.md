# MACman, in plain English

Confused? Read only this file. For the full list of what it can do today, see [COMMANDS.md](COMMANDS.md). Ignore DESIGN.md, ROADMAP.md, and
MANUAL_TASKS.md until you want detail.

---

## What it is

**You text your Mac. It does the thing. It texts you back.**

That's it. You're at a café, you remember you left something running at home,
you text your own Apple ID, and your Mac does it.

---

## How it works — three parts

**1. The door.** So random people can't command your Mac.
You say a wake phrase ("MACman wake up"), then send a 6-digit code from your
authenticator app. Only then does it listen.

**2. The brain.** Something has to decide what to do.
There are two, and MACman picks automatically:

- **The private brain** — a model running *on your Mac*. Free. Works offline.
  Nothing ever leaves your computer. Used for anything personal: your
  documents, your mail, your files.
- **The Claude brain** — smarter, costs money, needs internet. Used for coding
  and hard problems.

The rule: *anything personal stays on your Mac.* Always. Automatically.

**3. The hands.** How it actually does things.
Mostly by running commands (like typing in Terminal for you) or telling apps
what to do. Clicking on screen is a last resort, because clicking is
unreliable.

---

## What works right now

| Thing | Status |
|---|---|
| Reading your texts | ✅ works |
| Sending you texts | ✅ works |
| The door (wake phrase + code) | ✅ works |
| Deciding private vs Claude | ✅ works |
| **The private brain (Apple, built into your Mac)** | ✅ **works — nothing to install** |
| The Claude brain | ❌ needs a key (optional, for harder tasks) |
| Calling your Mac on FaceTime | ❌ not built yet |

**MACman works today.** Text it, it answers — free, offline, nothing downloaded.

The private brain is Apple's own on-device model. It ships with macOS 26, so
there is no download, no background app, and no disk cost. *(An earlier version
used Ollama, which needed 5 GB and a running server. That's gone.)*

---

## The one confusing thing: permissions

macOS makes you approve access. **The approval belongs to the app you run
MACman from** — usually Terminal.

So: **always run MACman from Terminal.** If you run it somewhere else, it will
say permissions are missing even though you granted them. That's not a bug.

---

## What to do next

### Step 1 — start MACman

Open Terminal and run:

```bash
cd ~/Documents/MACMan && .venv/bin/python -m macman.main serve
```

That's it. No second window, no server to start first.

### Step 2 — text your Mac

From another Apple device, to your own Apple ID:

1. `MACman wake up`
2. the 6-digit code from your authenticator app
3. `how many PDF files are in my Downloads folder?`
4. `end session` when done

That's the whole product working.

---

## If you only remember one thing

One command:

```bash
.venv/bin/python -m macman.main serve
```

Then text your Mac from another Apple device.

---

## Optional, later

- **Claude brain** — a key from console.anthropic.com. Only needed for harder
  things: understanding images, apps that can't be scripted, coding. Everything
  personal works without it.
- **More abilities** — Pages, Spotify, browsers and more are being built
  next; see [ROADMAP.md](ROADMAP.md).
- **FaceTime calling** — planned, not built.
- **Sharing it with others** — later.

None of these are needed for it to work today.
