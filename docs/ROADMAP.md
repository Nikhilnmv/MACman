# MACman — Roadmap

The single plan. Merged from the old `ROADMAP.md` and the private
`EXPERIENCE_PLAN.md`, because two documents describing the same project drifted
apart and one of them was gitignored where nobody would check it.

**Goal:** an open-source tool that privacy-minded Mac users actually run every
day. Not a company, not a demo — something real people install and keep.

**Audience:** developers and technical Mac users.

**Legend:** 🤖 I build · 👤 you do · ✅ done · ⏸ blocked · ⬜ not started

---

## Where we are

```
Foundation + text channel   ████████████████████  done, verified live (Aug)
Capabilities (18 tools)     ████████████████████  done, 99% selection
Security, attacked          ████████████████████  84 audit checks, all held
Consent before anything     ████████████████████  one exit, dialog + text
MACman.app                  ████████████████░░░░  built; not yet wired to the poller
Actually usable end-to-end  ████████░░░░░░░░░░░░  the app does not answer texts yet
Local voice                 ████████████████░░░░  works; unverified by you
FaceTime calling            ██████████░░░░░░░░░░  audio + auth proven; driver needs a call
Distribution & updates      ░░░░░░░░░░░░░░░░░░░░  nothing: no tags, no cask, no update path
```

---

## The honest picture

Four things are true at once, and the plan only makes sense if all four are
held.

**The engine works.** 99% tool selection over 105 trials, 100% answer
correctness where checked against ground truth, zero network calls during
private tasks.

**The security core is real.** One exit for data, consent before anything
leaves, 84 audit checks across four suites. Two genuine vulnerabilities were
found by attacking it — a case-insensitive credential leak, and a consent path
where every refusal would have been recorded as an approval.

**The product is not connected to itself.** `MACman.app` runs the bridge —
status, settings, activity, consent, setup — and **never starts the iMessage
poller**. That lives in `macman serve`, which the app has no path to. So the
menu bar says "Running" when nothing is listening, and actually using MACman
still means a Terminal command, which puts the permission back on Terminal and
undoes the reason the app was built. **Fixing this is checkpoint 3, step 1.**

**Nobody has used it.** 908 tasks in the audit log, all `cli/unspecified`. Zero
on the iMessage channel since 16 August, before the app existed. Every number
in `RELIABILITY.md` came from one developer on one Mac.

The defensible position — and the only one worth building for — is this:
**nothing else routes your personal documents to an on-device model and proves
it.** Anthropic's own remote-Mac feature tells users to avoid sensitive data.
That gap is the whole product.

---

## Checkpoint 1 — Trust it ✅

### 1a. Daily use *(still open, and still yours)*

| | Task | State |
|---|---|---|
| 👤 | Run [VERIFY.md](VERIFY.md) end to end from your phone | ⬜ |
| 👤 | Use it daily for a week — real tasks, not test ones | ⬜ |
| 🤖 | Fix what surfaces | ⬜ |

**Why it matters:** you are the only person who can say whether this is
*useful*, as opposed to *correct*. The benchmark measures whether it picks the
right tool, not whether anyone wanted the result.

### 1b. Adversarial security testing ✅

| | Task |
|---|---|
| ✅ | Prompt injection: hostile text in file content and filenames — fake system turns, forged authority, urgency, tool-shaped payloads |
| ✅ | Reach `~/.ssh` by indirection — traversal, symlink, case variants |
| ✅ | Talk past a confirmation gate |
| ✅ | Force a private task to the cloud |
| ✅ | Dependency audit: all 11 pinned by hash, each justified |
| ✅ | Swift helper network check (a Python audit cannot see their sockets) |
| ✅ | [SECURITY.md](SECURITY.md) — the threat model, including what MACman does **not** protect against |

**23 attack vectors, 23 resisted** — but only after the suite found a **real
vulnerability**. macOS filesystems are case-insensitive, so `~/.SSH/id_ed25519`
returned live private key material while the audit scored it as passing,
because the audit compared spellings too. Fixed with three independent checks;
the suite now scores by inode identity.

Two corrections fell out of it: the free tier is **11 packages, not 17**, and
on-device inference and speech were verified to open **no sockets** at runtime,
closing a caveat that had rested on Apple's documentation.

---

## Checkpoint 2 — MACman.app ✅

### Why the app is a security improvement, not a coat of paint

Three things got better, and the third settles it.

**The localhost attack surface stopped existing.** No port, no token, no origin
checks, no DNS-rebinding defence, no CSRF. Not mitigated — absent.

**The browser left the trust path.** Consent is a native dialog, and an
extension cannot read or click an `NSAlert`.

**The permission grant narrowed from Terminal to MACman.** macOS attributes a
permission to the **responsible process** — the app that launched the one
asking. Granting Full Disk Access to Terminal does not give it to MACman; it
gives it to *every script you will ever run in a shell*. Granting it to
`MACman.app` gives it to MACman alone.

> **Architectural constraint, not a preference:** the daemon must be a **child
> of the app**. Started by `launchd`, the responsible process becomes `launchd`,
> the permission attaches to a bare binary with no bundle, and the benefit
> evaporates. It follows that **quitting the app stops MACman** — the UI must
> say so rather than dying quietly.

### Architecture

```
MACman.app                          ← owns TCC grants, one stable identity
├── Contents/MacOS/MACman           ← SwiftUI: menu bar, settings, consent
├── Contents/Resources/
│   ├── python/                     ← embedded CPython, pinned by SHA256
│   ├── daemon/macman/              ← the daemon
│   └── helpers/                    ← macman-local, -speech, -audio
└── spawns as a CHILD ↓
    macman bridge (Python)          ← JSON over pipes, nothing binds a port
        ├── status · settings · activity · consent · setup
        └── iMessage poller         ← MISSING — checkpoint 3, step 1
```

### What shipped

| | Phase | What | State |
|---|---|---|---|
| 🤖 | A | `egress.py` — one exit: describe, authorise, record | ✅ 21/21 |
| 🤖 | B | Both senders wired through it; consent over text | ✅ |
| 🤖 | C | Bundle, embedded Python, daemon as child, pipe IPC | ✅ 70 MB, self-contained |
| 🤖 | D | Native consent dialog | ✅ 20/20 |
| 🤖 | E | Settings: permissions, allowlist, engine, Keychain key | ✅ |
| 🤖 | F | Activity view — what ran, what left | ✅ |
| 🤖 | G | Setup wizard | ✅ |

**Verified, not assumed:** Full Disk Access granted to `MACman.app` reaches the
child daemon; the daemon runs as a direct child (`ppid` matches); the bundle
runs entirely from its own Python, with all 34 reachable modules importable and
the developer's `site-packages` excluded.

**Four bugs found by running rather than reading**, each invisible on a
developer's machine:

- `MenuBarExtra` has no launch hook, so the daemon never started until someone
  clicked the icon — the app looked fine and did nothing.
- SIGPIPE would have killed the app when the daemon died, taking down the only
  UI able to report it.
- The runtime pin selected the *freethreaded* Python, which has no pyobjc
  wheels — the install would have failed after a 30 MB download.
- The consent reply crossed the pipe as the string `"false"`, and Python's
  `bool("false")` is `True`. **Every refusal would have been an approval.**

---

## Checkpoint 3 — Release candidate ← **we are here**

Turning a development project into something a stranger could install, use, and
remove. Two items are not "incomplete" but **wrong**: one misleads you about
whether MACman is running, the other leaves your API key behind after "revoke
everything."

### Decisions taken

| | |
|---|---|
| **Poller** | The app owns it — the bridge starts the serve loop |
| **Updates** | Homebrew cask now; in-app checker later |
| **Fresh-install test** | A separate macOS user account |
| **Signing** | Deferred to step 6, decided after seeing the fresh install |

### The steps

| | Step | What | State |
|---|---|---|---|
| 🤖 | 0 | Merge the two roadmaps into this document | ✅ |
| 🤖 | 1 | **Wire the poller into the bridge** — the blocker | ⬜ |
| 🤖 | 2 | Make revoke actually revoke, and work without the repo | ✅ |
| 🤖 | 3 | Rewrite TESTING.md and VERIFY.md as real-user guides, including removal | ⬜ next |
| 🤖 | 4 | Permissions cleanup guide — Claude.app, Terminal, MACman.app | ⬜ |
| 👤 | 5 | Fresh install in a second macOS account | ⬜ |
| 🤖 | 6 | Homebrew cask, version single-sourced, first release | ⬜ |
| 🤖 | 7 | Final re-scored evaluation | ⬜ |

**Step 1 — the poller.** The serve loop runs on a worker thread inside the
bridge. Three things must hold:

- **A poller crash must never kill the bridge**, or you lose the only surface
  that could tell you it died.
- **The menu bar must stop saying "Running"** when nothing is listening. It
  should distinguish *listening*, *not listening because Full Disk Access is
  off*, and *stopped*.
- `macman serve` keeps working unchanged, for CLI users and for debugging.

Consent-over-text becomes reachable from the app for the first time. Which
surface asks is decided by where the request came from: a message asks over
iMessage, anything local asks in a dialog.

**Step 2 — revoke.** Currently deletes the TOTP secret only. It must also take
the Claude key (`com.macman.cloud`, added in phase E), the app bundle, the
state directory and the login item, and drop the dead LaunchAgent path.
Critically it must work **without the repository** — someone who installed a
cask has no `.venv` to run a script from.

**Step 4 — permissions.** Honest limit: `TCC.db` is protected and cannot be
read, so this will be exact per-pane instructions and behavioural probes, not
an automated audit.

**Step 5 — fresh install.** A second macOS account gives a genuinely clean TCC
database and home folder without risking your working setup. Check early
whether iMessage in that account is workable — it needs an Apple ID signed in.

**Step 6 — the correction that matters.** `brew install --cask` **does** apply
`com.apple.quarantine`; it is source *formula* installs that do not. So an
unsigned `MACman.app` installed by cask will show "unidentified developer"
unless the user passes `--no-quarantine`. Telling a security-conscious user to
bypass Gatekeeper for a Full-Disk-Access tool undercuts everything else here.

| Path | Cost | First impression |
|---|---|---|
| Cask + sign & notarize | $99/yr | Clean, no warning |
| Cask, unsigned | £0 | Gatekeeper warning, or `--no-quarantine` in the docs |
| Formula (source build) | £0 | No warning, but needs Xcode and a build |

**Done when:** someone who has never seen this can install it, use it, and
remove it completely, without you helping.

---

## Checkpoint 4 — FaceTime ⏸ *(needs a second Apple device)*

**Already proven:** Core Audio process taps capture one app's audio without
touching system output (1.4 MB at 48 kHz). Speaking into BlackHole works with
speakers untouched. Transcription survives call audio. A spoken code is
converted to digits, which closed a credential leak *before* the channel
existed.

| | Task | State |
|---|---|---|
| 👤 | **Second Apple device** — the hard blocker | ⬜ |
| ✅ | Code over voice | ✅ |
| ✅ | Transcription adequate for call audio, errors change no actions | ✅ |
| ⏸ | `channels/facetime` — place and answer calls | ⏸ |
| ⏸ | Wire tap → transcription → engine → BlackHole into a live loop | ⏸ |
| ⏸ | Barge-in: stop talking when interrupted | ⏸ |

| Experiment | Status | Fallback |
|---|---|---|
| Does `AutoAcceptInvites` still work on macOS 26? | ⏸ needs a call | click Accept via Accessibility |
| Do taps capture FaceTime specifically? | ⏸ needs a call | BlackHole as system output |
| Is transcription accurate on compressed call audio? | ✅ **yes** | — |
| Is FaceTime's Accessibility tree drivable? | ⚠ partly | URL schemes only |

**The transcription result went against my prediction.** AAC-ELD at 24 kbps
costs nothing, 10% packet loss costs nothing, and only noise below ~10 dB SNR
degrades anything — worst case 9.8% WER on a deliberately terrible call. And
those errors **changed no actions**: degraded transcripts scored 17/18 correct
against 16/18 clean. Voice over FaceTime does not need cloud speech-to-text.

**The Accessibility result is now the risk.** The tree is readable (19 nodes,
7 buttons) but **4 of 7 buttons carry no label**, addressable only by tree
position — the weakness that measured 50% and got clicking dropped from
production. Everything turns on one question: **is Accept labelled during an
incoming call?** If not, the only fallback is `AutoAcceptInvites`, which answers
*everyone who calls*. I would rather ship FaceTime late than default to that.
It is deliberately not set on this Mac.

**Why the rest is paused rather than in progress:** every remaining item is
verified by making a call. A call driver that has never answered a call is code
that compiles, reads well, and fails on first contact.

---

## Checkpoint 5 — Launch

| | Task |
|---|---|
| 🤖 | Landing page: own domain, static, **no analytics and no third-party requests** — a privacy tool whose site loads Google Fonts is not credible |
| 🤖 | Lead with proof: "nothing leaves your Mac", with the commands to verify it |
| 🤖 | Honest ceiling: what a ~3B on-device model cannot do |
| 👤 | Check `macman.sh` / `getmacman.com` / `macman.app` for availability and name conflicts |
| 👤 | Announce — Hacker News, r/macapps, Mac communities |
| 👤 | Decide what support burden you will carry |

`docs/index.html` should be deleted rather than improved; it describes a
different product.

**Realistic outcome:** a few hundred stars, a handful of daily users, some
issues. That is a good result for an honest tool in a crowded space.

---

## Later, and honestly optional

| | Why it is not prioritised |
|---|---|
| In-app updater (Sparkle) | Wants a signed app, and an update channel is attack surface for a tool holding Full Disk Access |
| Level 4 (vision, unscriptable apps) | Needs a funded API key; genuinely paid |
| More primitives | Each is hand-written; add them when a real user asks |
| AirDrop | No scriptable path; UI automation measured 50% and would send the wrong file to the wrong person |

---

## What could kill this

| Risk | Honest assessment |
|---|---|
| **Nobody installs it** | Most likely outcome. Six permissions is a big ask from an unknown author |
| **A security incident** | Fatal to trust, and could genuinely harm someone. Why checkpoint 1 came first |
| **No way to ship a fix** | Real today: no tags, no cask, no update path. A security fix would reach nobody |
| **Apple or Anthropic ship it better** | Anthropic already has remote Mac control. Compete on privacy, not features |
| **You lose interest** | It is already useful to you. Checkpoint 1 alone leaves something worth keeping |
| Apple changes an API | AppleScript is stable; Accessibility is not, which is why we barely use it |

---

## Open questions

1. **Domain name**, and has "MACman" been checked against existing Mac tools?
2. **Signing** — $99 now, or ship unsigned and accept the Gatekeeper warning?
3. **Does the CLI stay a first-class path?** It currently means every feature
   needs two front ends, and `appsettings`/`appactivity`/`appsetup` are already
   app-only.

Settled, and recorded so they are not re-litigated:

- **Pre-approvals expire** — 90 day cap. Consent that never expires becomes a
  setting nobody remembers choosing.
- **Activity shows no new data.** The audit log already stores result hashes
  rather than content, and excludes note bodies and message text. A view that
  captured more would be a second copy of your data made to reassure you about
  the first.
- **Consent never lives in a window** — a browser extension can read and click
  a page, but not an `NSAlert`.

---

## How we work

- **Measure before believing.** Several assumptions about the on-device model
  were wrong; repeated trials caught each one. Single runs mislead.
- **Typed arguments, never composed strings.** 8/8 versus 1/5, and a security
  property as much as a reliability one.
- **Fail loudly at the boundary.** "That needs a key" beats a confident wrong
  answer, every time.
- **Publish the numbers that look bad.** The 50% accessibility result is why we
  do not click things. Hiding it would invite someone to re-propose it.
- **A test that has never failed has not passed.** Two audits scored perfectly
  while the thing they guarded was broken.
