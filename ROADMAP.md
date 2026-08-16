# MACman — Roadmap

What's built, what's next, and who does each part.

What it can do today: [COMMANDS.md](COMMANDS.md).
Measured reliability: [RELIABILITY.md](RELIABILITY.md).
Architecture and the free/paid line: [CAPABILITY.md](CAPABILITY.md).
Design rationale: [DESIGN.md](DESIGN.md). Your checklist:
[MANUAL_TASKS.md](MANUAL_TASKS.md). Confused? [START_HERE.md](START_HERE.md).

**Legend:** 🤖 I build it · 👤 you do it · ✅ done · ⏸ blocked · ⬜ not started

---

## Where we are

```
v0  Foundation           ████████████████████  done
v1  iMessage control     ████████████████████  done — verified over real iMessage
v2  Capability build-out ██░░░░░░░░░░░░░░░░░░  next — Level 1 & 2 primitives
v3  Voice + FaceTime     ░░░░░░░░░░░░░░░░░░░░  not started
v4  Ship as an app       ░░░░░░░░░░░░░░░░░░░░  not started
```

**MACman works today.** You text your Mac, it authenticates you, routes the
task, answers from an on-device model, and replies — verified end to end over
real iMessage at zero cost.

What's left is **breadth**: the Level 1–2 primitives that turn a working
skeleton into something you'd use daily.

---

## ✅ v0 — Foundation *(complete)*

| | Component | Status |
|---|---|---|
| 🤖 | `config` / `userconfig` — settings in TOML, nothing personal in source | ✅ |
| 🤖 | `lockstate` — capability tiers from screen lock | ✅ |
| 🤖 | `preflight` — permission checks with deep links | ✅ |
| 🤖 | `audit` — append-only, fsync'd, every tool call | ✅ |
| 🤖 | `guard` — deny / confirm / allow | ✅ tested |
| 🤖 | `router` — local vs cloud, decided **without any network call** | ✅ 20/20 |
| 🤖 | `auth` — TOTP, lockout, replay protection | ✅ 9/9 |
| 🤖 | Tools: shell, applescript, ui (Accessibility), screen | ✅ |
| 🤖 | `engines/cloud` — Claude via Tool Runner, caching, cost tracking | ✅ built, ⏸ never run (no key) |
| 🤖 | `engines/local` — Apple FoundationModels + typed tools | ✅ **8/8 measured** |
| 🤖 | Swift helpers: `macman-state`, `macman-ax`, `macman-local` | ✅ built with `-DMACMAN_TOOLS` |
| 🤖 | `main` CLI — setup, preflight, route, run, repl, serve, auth | ✅ |
| 🤖 | `revoke_all` — one command to disable everything | ✅ |
| 👤 | Fix Swift toolchain · install Xcode · accept licence | ✅ |
| 👤 | Grant Accessibility, Full Disk Access, Screen Recording | ✅ |

**Removed along the way:** Ollama. Its only advantage was tool calling, at the
cost of 5 GB, a daemon, heavy RAM and battery, and ~10× the latency. Once
Apple's model gained tools it had nothing left to offer.

## ✅ v1 — iMessage control *(complete, verified live)*

| | Component | Status |
|---|---|---|
| 🤖 | `channels/imessage` — WAL-aware poller, replay + backfill guards | ✅ verified on real data |
| 🤖 | `session` — allowlist → kill switch → wake phrase → auth → route | ✅ |
| 🤖 | Wake phrase, customisable | ✅ |
| 🤖 | `channels/confirm` — confirmation over text, fails closed | ✅ 6 scenarios |
| 🤖 | `serve` daemon, plus `--dry-run` | ✅ |
| 🤖 | Screenshot attachments, only on task replies | ✅ |
| 👤 | Provision TOTP, add handle, live test from a phone | ✅ |

**Bugs found and fixed during v1** — worth remembering, all were silent:

1. `chat.db` opened `immutable=1`, which skips SQLite's WAL — MACman was
   structurally blind to recent messages.
2. A replayed TOTP code mid-session would have been run as a task; on a
   cloud-routed session that means sending a credential to the API.
3. Attachments failed silently from `Application Support`; Messages only reads
   from places like `~/Pictures`.
4. The `serve` local path didn't pass the confirmation callback, so a guarded
   action would have blocked the daemon on stdin nobody was watching.

---

## ⬜ v2 — Capability build-out *(next)*

Turning four working levels into broad coverage. **This is where MACman becomes
worth using every day.**

### Level 1 — Native macOS · free · ✅ **done**

| | Task | |
|---|---|---|
| 🤖 | `system_control` — lock, sleep, restart, shutdown, volume, mute, brightness | ✅ |
| 🤖 | `network_control` — Wi-Fi on/off/status/list/join, Bluetooth | ✅ |
| 🤖 | `file_operation` — move, copy, rename, trash, compress, make_folder | ✅ |
| 🤖 | Query tools — count, list, find, read, system facts, app info | ✅ |
| 🤖 | AirDrop send | ⬜ **deferred** — no scriptable path; see COMMANDS.md |
| 🤖 | Settings panes by URL scheme | ⬜ |

### Level 2 — Application automation · mostly free

| | Task |
|---|---|
| 🤖 | Generic `applescript_action` primitive over app dictionaries |
| 🤖 | **Shortcuts primitive** — `shortcuts run`, covers apps without AppleScript |
| 🤖 | **URL-scheme primitive** — `whatsapp://`, `vscode://`, skips UI entirely |
| 🤖 | Pages / Numbers / Keynote: open, edit fields, export PDF |
| 🤖 | Browser: open, navigate, search, read page, run JavaScript |
| 🤖 | Media: play, pause, skip, volume for Music and Spotify |
| 🤖 | ✅ `media_control`, `browser_control`, `document_control`, `run_shortcut` |
| 🤖 | ✅ Experiment: label beats path 3× — recorded, still below the bar |
| 🤖 | ✅ Fixed selection collisions by **merging** `network_control` into `system_control` — 88% → **97%** |
| 🤖 | Mail drafting, Calendar events, Notes/Reminders creation |

### Level 3 — AI app orchestration · mixed

| | Task |
|---|---|
| 🤖 | Open app, open project (CLI / URL scheme) — the free half |
| 🤖 | Navigate to session + type prompt — needs the label experiment first |
| 👤 | Confirm automating Claude.app / Codex is within their terms |

### Level 4 — Claude fallback · paid

| | Task |
|---|---|
| 🤖 | Upgrade prompt when a task crosses the free boundary |
| 🤖 | Hand-off: Claude drives the same primitives, not a separate tool set |
| 👤 | **Add an API key** — the cloud engine has still never run |

**Done when:** the free tier covers everyday system, file, document, browser
and media work, and the paid boundary is a clear message rather than a failure.

---

## ⬜ v3 — Voice and FaceTime

| | Task |
|---|---|
| 🤖 | Swift `macman-audio` — Core Audio process tap + BlackHole playback |
| 🤖 | Swift `macman-speech` — `SFSpeechRecognizer`, `AVSpeechSynthesizer` |
| 🤖 | VAD, turn-taking, barge-in |
| 🤖 | `channels/facetime` — AX state machine with verification per step |
| 🤖 | Two-brain loop: fast narration while the work runs |
| 👤 | `brew install blackhole-2ch`, set FaceTime's microphone |
| 👤 | A second Apple device to call from |

**Four experiments here**, each with a known fallback: does `AutoAcceptInvites`
still work on macOS 26; do Core Audio taps see FaceTime specifically; is
on-device speech accurate on compressed call audio; is FaceTime's Accessibility
tree rich enough to drive.

## ⬜ v4 — Ship

| | Task |
|---|---|
| 🤖 | `MACman.app` — signed menu-bar app owning its own permissions |
| 🤖 | LaunchAgent, permissions wizard, arm/disarm, kill switch |
| 🤖 | README, LICENSE, `.gitignore`, personal-data pass → GitHub |
| 👤 | Self-signed identity; re-grant permissions once |

**Not App Store** — Accessibility, Full Disk Access and cross-app screen
recording are all forbidden to sandboxed apps. Direct distribution only.

---

## How we work

- **Measure before building.** Three assumptions about the on-device model were
  wrong this month; each was caught by testing with repeated trials rather than
  single samples. Single runs mislead — this model is non-deterministic.
- **Typed arguments, never composed strings.** The difference between 1/5 and
  8/8, and a security property as much as a reliability one.
- **Every phase ships something usable.** v1 works without v2. v2 works
  without v3.
- **Fail loudly at the boundary.** A clear "that needs a key" beats a confident
  wrong answer.

## Standing risks

| Risk | Mitigation |
|---|---|
| Apple changes an app's UI | Prefer AppleScript/Shortcuts over Accessibility; golden fixtures |
| `chat.db` schema changes | Columns detected at runtime |
| On-device model too weak for a task | Honest boundary message, never a silent guess |
| Cost runs hot on Claude | Level 1–2 keep most work free and deterministic |
| Prompt injection | Typed arguments — no command string to word around |
| Automating third-party AI clients | Confirm terms before depending on it |
