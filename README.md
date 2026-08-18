# MACman

**Text your Mac. It does the thing. It texts you back.**

You're out. You remember something you left running at home. You text your own
Apple ID, and your Mac does it.

```
you  →  MACman wake up
mac  →  MACman here. Send your code.
you  →  482913
mac  →  MACman ready. Mac is unlocked — full access. What do you need?
you  →  how many PDF files are in my Downloads folder?
mac  →  There are exactly 25 PDF files in /Users/you/Downloads.
you  →  end session
mac  →  Stopped. 1 task this session, $0.000.
```

That `$0.000` is the point. **The everyday half runs entirely on your Mac**,
on Apple's built-in on-device model — no download, no daemon, no API key,
nothing sent anywhere.

---

## Why this exists

Remote-control tools make you squint at a tiny copy of your screen and tap
around. Cloud assistants want your documents. MACman is neither:

- **Your personal files never leave your Mac.** Documents, mail, notes and
  calendar are handled by Apple's on-device model. The router that decides this
  **never makes a network call** — asking a cloud model "is this private?" has
  already leaked the filename.
- **Free for the things you do daily.** No subscription, no account, no key.
- **Claude only when you want it**, with your own key, for the things a small
  local model genuinely cannot do: reading code, fixing bugs, understanding
  images.

Inspired by [FaceTimeOS](https://github.com/dylanelu/FaceTimeOS) (1st place,
Cal Hacks 12.0), rebuilt with different engineering priorities — see
[DESIGN.md](docs/DESIGN.md) for the specific differences.

---

## What it can do

| | |
|---|---|
| **Files** | count, list, find, read, move, copy, rename, trash, compress |
| **System** | lock, sleep, volume, brightness, Wi-Fi, Bluetooth, disk, battery |
| **Apps** | open anything · Music & Spotify · browsers · Pages, Numbers, Keynote |
| **Personal** | Mail (read + draft) · Calendar · Notes · Reminders |
| **Developer** | open projects in VS Code · hand coding tasks to Claude Code |
| **Voice** | talk to your Mac and it talks back — on-device, offline |

Full list with the phrasings that work: **[COMMANDS.md](docs/COMMANDS.md)**

**18 primitives. 99% tool-selection accuracy**, measured over 105 trials —
[RELIABILITY.md](docs/RELIABILITY.md) has every number and the method.

---

## Requirements

- **macOS 26** with Apple Intelligence enabled (the free engine)
- **A second Apple device** — iPhone or iPad — to text from. iMessage routes to
  *devices*, so you need something other than the Mac itself.
- **Xcode** to build the Swift helper *(Command Line Tools alone is not enough
  — Apple's tool-calling macros ship only with full Xcode)*
- Optional: an [Anthropic API key](https://console.anthropic.com) and
  `pip install '.[cloud]'` for the Claude tier

A one-line `brew install` is planned — see
[packaging/README.md](packaging/README.md). It needs the repo to be public,
since Homebrew fetches a public tarball.

---

## Install

```bash
git clone git@github.com:Nikhilnmv/MACman.git
cd MACman
python3 -m venv .venv && .venv/bin/pip install -e .   # 11 packages, no cloud SDK
cd helpers && swift build -c release -Xswiftc -DMACMAN_TOOLS && cd ..
.venv/bin/python -m macman.main setup
```

`setup` walks the permissions, your allowlist, and your login code, then
self-tests. Then:

```bash
.venv/bin/python -m macman.main serve
```

Text your Apple ID from your phone. **Step-by-step with expected output at
every stage: [TESTING.md](docs/TESTING.md)**

Or talk to it directly, no phone needed:

```bash
.venv/bin/python -m macman.main voice
```

Transcription and speech both run on-device — nothing is uploaded, and it works
with no network.

---

## Security

This is a program that acts on your Mac when someone texts it, so the
boundaries are worth stating plainly.

- **It can lock your Mac. It can never unlock it.** macOS blocks synthetic
  input at the lock screen, and MACman holds no password. The worst case for a
  lost session is a *more* locked Mac.
- **Your login password is never used, requested or stored.** Sessions are
  authenticated with a TOTP code from your authenticator app — revocable
  without touching anything about your Mac.
- **Only handles you allowlist get through**, checked before any model sees the
  message. Everything else is dropped in silence.
- **Destructive actions ask first**, every time. Deleting goes to the Trash.
- **Credential paths are refused in code** — `~/.ssh`, `~/.aws`, your Keychain.
  Not by prompting, so a jailbreak isn't enough.
- **Every tool call is logged** to an append-only audit file.
- **One command turns everything off**: `scripts/revoke_all.py --revoke`

These are attacked rather than asserted — **23 attack vectors, 23 resisted**
(`tests/audit/injection.py`). One of them found a real credential leak before
it found nothing.

**Before you install this, read [SECURITY.md](docs/SECURITY.md)** — especially
[what it does *not* protect you from](docs/SECURITY.md#what-macman-does-not-protect-you-from).
It needs Full Disk Access, it has been reviewed by exactly one person, and both
of those facts are yours to weigh.

---

## How it works

Four levels, cheapest and most reliable first:

```
Level 1  Native macOS      shell, file operations           free
Level 2  App automation    AppleScript · Shortcuts · URLs   free
Level 3  Dev tools         code CLI · claude CLI            free / your key
Level 4  Claude fallback   vision, novel apps, recovery     your key
```

Every primitive takes **typed arguments** — the model picks an action and fills
a field, and Python builds whatever runs. That measured **8/8** correct against
**1/5** when the model wrote shell commands itself, and it means a malformed
command is impossible rather than merely discouraged.

Accessibility-based UI clicking measured **50%** and is deliberately *not*
used. A wrong click isn't a wrong answer — it presses something.

---

## Documentation

| | |
|---|---|
| [SECURITY.md](docs/SECURITY.md) | Threat model, and what it does **not** protect against |
| [TESTING.md](docs/TESTING.md) | Set up and verify everything, step by step |
| [COMMANDS.md](docs/COMMANDS.md) | Everything it can do, and how to ask |
| [RELIABILITY.md](docs/RELIABILITY.md) | Every measurement, with the method |
| [CAPABILITY.md](docs/CAPABILITY.md) | Architecture, and the free/paid boundary |
| [DESIGN.md](docs/DESIGN.md) | Why it's built this way |
| [ROADMAP.md](docs/ROADMAP.md) | What's done, what's next |

---

## Status

**Working:** iMessage control, 18 primitives, on-device engine, security model.

**Voice works locally** — `macman voice` listens, acts and answers aloud using
Apple's on-device speech, entirely offline.

**Not built yet:** FaceTime calling (the remaining half of v3), a signed
menu-bar app (v4).

**Claude Code handoff (Level 3) works**, verified — `claude_code` calls the
`claude` CLI, which authenticates with a Claude Pro/Max subscription. No
metered API key needed for coding tasks handed to Claude Code.

**The direct Claude API (Level 4 — vision, cross-app orchestration, the
`engines/cloud.py` fallback) is still untested.** The Messages API is metered
separately from a Pro/Max subscription and needs a funded `sk-ant-...` key;
none has been configured, so its cost and latency remain unmeasured.

MIT licensed. Contributions welcome, especially measurements that contradict
the ones in [RELIABILITY.md](docs/RELIABILITY.md).
