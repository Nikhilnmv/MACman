# Capability map

What MACman can do, what needs a Claude key, and what isn't practical —
measured on this Mac, not claimed.

Supersedes the earlier `VISION_FEASIBILITY.md`, now merged here.

---

## 1. The architecture, in four levels

MACman is a **remote natural-language control layer for macOS**. It does not
replace the tools on your Mac; it lets you control and combine them remotely.

```
                        Natural language
                              │
                    ┌─────────┴─────────┐
                    │  Intent → action  │   ← the model's only job
                    └─────────┬─────────┘
                              │
   ┌──────────────┬───────────┴──────────┬──────────────────┐
   ▼              ▼                      ▼                  ▼
 LEVEL 1        LEVEL 2               LEVEL 3            LEVEL 4
 Native        Application          AI application       Fallback
 macOS         automation           orchestration        (Claude)
   │              │                      │                  │
 typed        AppleScript ·          open · navigate ·   vision ·
 primitives   Shortcuts · URL        type · submit       novel work
              schemes · AX
```

**Small toolset, large reachable surface.** Not `count_pdf_files()` and
`send_whatsapp_file()` as thousands of isolated tools, but composable
primitives the model assembles:

```
System       execute action · query state · modify setting
Filesystem   find · read · write · move · copy · delete · compress
Application  open · focus · navigate · invoke action · enter text
AI app       open · open session · enter prompt · submit
```

---

## 2. What the on-device model is actually for

Measured on this Mac, Apple's `FoundationModels` (~3B, ships with macOS 26):

| Capability | Result |
|---|---|
| Choose a typed tool, fill its fields | ✅ **8/8** |
| Chain 2–3 tools in one request | ✅ **3/3** |
| Author shell commands | ❌ **1/5** |
| Construct Accessibility paths | ❌ **8/16** |
| See a screenshot | ❌ **impossible** — text-only, verified against the SDK |
| Text-only, no tools | ⚠️ ~1 in 4 benign requests spuriously refused |

**The pattern is consistent: reliable at *choosing*, unreliable at *authoring
syntax*.** Shell commands are syntax. Accessibility paths are syntax. Both fail.

This is exactly the role the refined vision assigns it — turn *"send this PDF
to Rahul"* into structured actions, not into a `grep` incantation. The
measurements support the architecture rather than fighting it.

**Consequence for design:** every Level 1–3 primitive must take **typed
arguments**, never a string the model composes. That is a security property as
well as a reliability one — arguments are validated in Python before anything
runs, so there is no command string for a prompt injection to word its way
around.

---

## 3. Level 1 — Native macOS · 🟢 free

Deterministic, typed, fast, no key. **This is where most everyday value lives.**

| Area | Mechanism | Status |
|---|---|---|
| Lock, sleep, restart, shut down | `pmset`, AppleScript | 🟢 free |
| Volume, mute, brightness | AppleScript | 🟢 free |
| Wi-Fi on/off, switch network | `networksetup` | 🟢 free |
| Bluetooth on/off, connect device | `blueutil` | 🟢 free |
| AirDrop enable, send file | Shortcuts / Finder scripting | 🟢 free |
| Open a Settings pane | `x-apple.systempreferences:` URL | 🟢 free |
| Change a setting inside a pane | Accessibility | 🔵 needs Claude |
| Files: create, move, copy, rename, delete, find, compress | shell / Finder | 🟢 free |

*"Lock my MacBook"*, *"Turn on AirDrop"*, *"Move the PDF from Downloads to
Documents/College"* — all free, all deterministic.

**Level 1 is ~90% free** and is the strongest part of the whole system.

---

## 4. Level 2 — Application automation · 🟡 mostly free

Four mechanisms, in descending order of reliability. **Always prefer the
highest one available for a given app.**

| Mechanism | Reliability | Free? |
|---|---|---|
| **AppleScript dictionary** | High — deterministic | 🟢 |
| **Shortcuts actions** | High — deterministic | 🟢 |
| **URL schemes** (`whatsapp://`, `vscode://`) | High for what they expose | 🟢 |
| **Accessibility navigation** | **~50% measured** | 🔵 Claude |

Two of these are underused and worth building on early:

* **Shortcuts** covers modern apps that never shipped AppleScript. `shortcuts
  run "<name>"` is deterministic and free.
* **URL schemes** open a specific chat, file, or project directly, skipping UI
  navigation entirely — `whatsapp://send?phone=…`, `vscode://file/…`.

### Per-app outlook

| App | Best mechanism | Free? |
|---|---|---|
| Finder, Mail, Calendar, Notes, Reminders | AppleScript | 🟢 |
| **Pages, Numbers, Keynote** | AppleScript (rich dictionaries) | 🟢 |
| Word, Excel, PowerPoint | AppleScript | 🟢 |
| Safari, Chrome | AppleScript + `do JavaScript` | 🟢 |
| Music, Spotify | AppleScript | 🟢 |
| **WhatsApp** | URL scheme opens a chat; **attaching a file needs UI** | 🟡 partly |
| Figma, Final Cut, Photoshop compositing | canvas — needs vision | 🔵/🔴 |

Your Pages example — *"open my resume, change CGPA to 8.4, export as PDF"* —
is **fully scriptable and free**. Pages exposes a rich AppleScript dictionary;
no vision or UI clicking is involved.

Your WhatsApp example is the honest split: opening the right chat is free via
URL scheme, but attaching a file means driving the UI, which measured 50%. That
one wants Claude until proven otherwise.

---

## 5. Level 3 — AI application orchestration · 🟡 mixed

The insight here is strong: **let each app's own agent do the work.** MACman
opens Claude, navigates to a conversation, types, submits — and Claude's own
model and tools take over. No API reimplementation.

| Step | Mechanism | Free? |
|---|---|---|
| Open the app | `open -a` | 🟢 |
| Open a project / file | URL scheme, CLI (`code .`) | 🟢 |
| Navigate to a specific conversation | Accessibility | 🔵 |
| Type a prompt and submit | Accessibility | 🔵 |

**Honest reading:** the *open* half is free; the *navigate and type* half needs
Claude, because it depends on the 50% path-construction weakness. That may
improve if elements can be addressed by label rather than path — untested.

Two things to check before building on this:

1. **Claude.app and Codex ship no AppleScript dictionary.** Automation means
   Accessibility, which breaks on app updates. Workable, not sturdy.
2. **Automating another vendor's client is worth checking against their
   terms** — a thing to confirm rather than discover later.

Also worth naming plainly: orchestrating Claude.app means the user pays for
Claude anyway. It relocates the cost rather than removing it.

---

## 6. Level 4 — Claude fallback · 🔵 paid

Where the previous levels can't reach:

- **Anything visual** — *"which photo has the product clearly visible?"*
  Impossible on-device, not merely unreliable.
- **Novel apps** with no scripting and an unfamiliar UI.
- **Multi-step orchestration** across applications with error handling.
- **Recovery** — an unexpected dialog, a slow load, a moved button. This is
  where every computer-use agent struggles today, including Anthropic's own,
  which is still research preview.
- **Code reasoning** — *"why is this failing?"*

---

## 7. The free / paid line

**Free tier — no key, no download, works offline:**

- All system operations: lock, sleep, volume, brightness, Wi-Fi, Bluetooth, AirDrop
- All file operations: create, move, copy, rename, delete, find, compress
- Documents: Pages, Numbers, Keynote, Office — open, edit fields, export
- Browsers: open, navigate, search, read pages, run JavaScript
- Media: Spotify, Apple Music — play, pause, skip, volume
- Mail, Calendar, Notes, Reminders — read and create
- Opening any application, and any project via CLI or URL scheme
- Remote access over iMessage, with wake phrase and TOTP

**Upgrade prompt — needs a Claude key:**

- Understanding anything visual
- Operating apps with no scripting interface (WhatsApp attachments, Figma)
- Navigating to a specific conversation inside Claude / Codex
- Multi-step cross-application orchestration
- Debugging, refactoring, code reasoning
- Recovering when something unexpected happens

That free set is **substantial and genuinely useful on its own** — which is the
stated goal. The upgrade prompt appears at a natural boundary the user can
understand: *"that needs eyes"* or *"that app can't be scripted."*

---

## 8. Design rules that follow from the measurements

1. **Typed arguments, never composed strings.** Took typed tools from 1/5 to
   8/8. Applies to every primitive at every level.
2. **Prefer AppleScript and Shortcuts over Accessibility.** Deterministic beats
   50%, and it's free.
3. **A wrong click is not a wrong answer — it presses something.** Anything
   under ~90% should not run unattended without confirmation.
4. **Apple's model gets its own prompt.** Claude's system prompt dropped tool
   use from 4/4 to 1/4 — its security language primes Apple's safety layer to
   refuse ordinary requests.
5. **Fail loudly at the tier boundary.** *"That needs a Claude key because it
   requires reading the screen"* is a good message. A wrong answer is not.
6. **Bet on Apple's model improving.** Each macOS release upgrades it for free;
   the primitives you write now get more capable without changes.

---

## 9. Open questions

1. ~~Can elements be addressed by label instead of path?~~ **Measured.** Label
   beats path roughly 3×: **50% vs 17%** (`tests/tasks/ax_label_vs_path.py`,
   4 scenarios × 3 trials, raw replies kept). Adding a parent hint did not
   help. With a better resolver — word-boundary matching, not substring —
   label mode would plausibly reach ~65–70%, but that is an estimate, not a
   measurement.

   **Rule that follows: if Accessibility is used at all, address elements by
   label, never by path.** Same principle as typed tools — don't make the
   model author syntax.

   **What it does not change:** even 70% is far below what unattended clicking
   needs, because a wrong click is not a wrong answer — it presses something.
   One trial chose "Cancel" where the answer was "Don't Save". Level 2–3
   Accessibility navigation still needs Claude.

   *Caveat:* the path baseline measured 17% here against 50% in an earlier run
   on the same trees. This model's run-to-run variance is large enough that
   formats must be compared within a single session to mean anything.
2. **How long a tool chain holds?** Three worked. Ten matters for §13-style
   orchestration.
3. **Claude's real cost and latency here.** Still entirely unmeasured — no
   valid key has ever been configured.
4. **Does the ~25% refusal rate persist with tools?** Measured text-only. Every
   tool-using trial has passed, hinting it is lower when reporting facts.
