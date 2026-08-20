# MACman — Roadmap

**Goal:** an open-source tool that privacy-minded Mac users actually run every
day. Not a company, not a demo — something real people install and keep.

**Two experiences matter equally:** texting your Mac from anywhere, and
FaceTiming it. Local voice is a bonus that happens to be useful when you're
sitting at the machine.

**Legend:** 🤖 I build · 👤 you do · ✅ done · ⏸ blocked · ⬜ not started

---

## Where we are

```
Foundation + text channel   ████████████████████  done, verified live
Capabilities (18 tools)     ████████████████████  done, 99% selection
Security, attacked          ████████████████████  23/23 + 21/21 egress
Consent before anything     ████████████████████  one exit, wired, dialog + 17/17
MACman.app                  ████████████████░░░░  menu bar + settings; no wizard yet
Local voice                 ████████████████░░░░  works; unverified by you
FaceTime calling            ██████████░░░░░░░░░░  audio + auth proven; driver needs a call
Ready for strangers         █████░░░░░░░░░░░░░░░  public repo; no setup UI yet
```

**What exists:** a working tool *you* can use. Text your Mac, it authenticates,
does the thing, texts back. 18 capabilities, free, private, measured.

**What doesn't:** anything a stranger could install, and any evidence it's
safe to hand to one.

---

## The honest picture

Three things are true at once, and the roadmap only makes sense if all three
are held:

**It works.** 99% tool selection over 105 trials, 100% answer correctness where
checked against ground truth, zero network calls during private tasks.

**It's unproven.** Every one of those numbers came from one developer testing
on one Mac. Nobody has used it for a week. Nobody has attacked it. Nobody has
installed it from scratch.

**Its ceiling is real.** The free tier runs a ~3B on-device model that cannot
see images, cannot write shell commands, and cannot operate apps offering no
automation. It does what we build primitives for. That is the shape of the
product, not a defect to fix.

The defensible position — and the only one worth building for — is this:
**nothing else routes your personal documents to an on-device model and proves
it.** Anthropic's own remote-Mac feature tells users to avoid sensitive data.
That gap is the whole product.

---

## Phase 1 — Trust it *(now)*

Nothing else matters until MACman is provably safe and you personally believe
it works. **Promoting a tool that holds Full Disk Access and has never been
attacked is the one mistake that could genuinely hurt someone.**

### 1a. You use it, daily, for a week

| | Task |
|---|---|
| 👤 | Run [VERIFY.md](VERIFY.md) end to end from your phone |
| 👤 | Use it daily for a week — real tasks, not test ones |
| 👤 | Keep a note of every wrong answer, awkward phrasing and confusion |
| 🤖 | Fix what surfaces |

**Why first:** you're the only person who can tell me whether this is *useful*,
as opposed to *correct*. The benchmark measures whether it picks the right
tool, not whether anyone wants the result.

### 1b. Adversarial security testing ✅

The defences were asserted, not attacked. Now they're attacked.

| | Task |
|---|---|
| ✅ | **Prompt injection suite** — hostile text in file content and filenames: fake system turns, forged authority, urgency, tool-shaped payloads |
| ✅ | Reach `~/.ssh` by indirection — traversal, symlink, case variants |
| ✅ | Talk past a confirmation gate |
| ✅ | Force a private task to the cloud |
| ✅ | Dependency audit: all **11** pinned with every hash, each justified |
| ✅ | Swift helper network check (the Python audit can't see their sockets) |
| ✅ | [SECURITY.md](SECURITY.md) — the threat model, including what MACman does **not** protect against |

**Result: 23 attack vectors, 23 resisted** — but only after the suite found a
**real vulnerability**. macOS filesystems are case-insensitive, so `~/.SSH/id_ed25519`
returned live private key material while the audit scored it as passing, because
the audit compared spellings too. Fixed in three independent checks; the suite
now scores by inode identity.

Two corrections came out of this beyond the fix itself:

- The free tier is **11 packages, not 17**. The old number was never measured
  after the cloud SDK was dropped.
- On-device inference and speech were verified to open **no sockets** at
  runtime, closing a caveat that previously rested on Apple's documentation.

**Still true and worth saying:** one developer, one Mac, no external review.
[SECURITY.md](SECURITY.md) says so in those words.

---

## Phase 2 — FaceTime *(needs your iPad)*

Co-priority with texting, and the thing you originally set out to build.

**Already proven:** Core Audio process taps capture one app's audio without
touching the rest of the system — verified, 1.4 MB captured at 48kHz.
On-device transcription is perfect on clean speech. Speaking into BlackHole
works, speakers untouched.

| | Task |
|---|---|
| 👤 | **Second Apple device** — the hard blocker; nothing below it works without one |
| ✅ | **Code over voice** — a spoken code carries no digits; converting it closed a credential leak *before* the channel opened |
| ✅ | Transcription proven adequate for call audio, and errors proven not to change actions |
| ⏸ | `channels/facetime` — place and answer calls, verified state per step |
| ⏸ | Wire tap → transcription → engine → BlackHole into a live call loop |
| ⏸ | Barge-in: stop talking when interrupted |

The wake phrase already exists and is channel-agnostic (`session.py`), so voice
inherits it. What was missing was the *code*, and that is done.

**Why the rest is paused rather than in progress:** every remaining item is
verified by making a call. Writing a call driver that has never answered a call
is how you get code that compiles, reads well, and fails on first contact —
and this project has already had three experiments produce confident wrong
numbers. Building it blind would be the fourth.

**Four experiments, each with a fallback:**

| Question | Status | If it fails |
|---|---|---|
| Does `AutoAcceptInvites` still work on macOS 26? | ⏸ needs a call | click Accept via Accessibility |
| Do taps capture FaceTime specifically? | ⏸ needs a call | BlackHole as system output, worse but works |
| **Is transcription accurate on compressed call audio?** | ✅ **yes** | — |
| Is FaceTime's Accessibility tree drivable? | ⚠ partly | URL schemes only; fewer controls |

**The third one resolved, against my prediction.** I said I'd bet against it.
AAC-ELD at 24 kbps costs nothing, 10% packet loss costs nothing, and only
background noise below ~10 dB SNR degrades anything — worst case 9.8% WER on a
deliberately terrible call. More to the point, **those errors changed no
actions**: replaying the degraded transcripts scored 17/18 correct against
16/18 clean. `Downloads → download` still produced `folder="Downloads"`.

Voice-over-FaceTime does not need cloud speech-to-text, and stays free.
Two earlier versions of that experiment were wrong before this one was right —
[RELIABILITY.md](RELIABILITY.md#call-audio--the-prediction-was-wrong) has both.

**The fourth is a partial, and it is now the risk.** The Accessibility tree is
readable (19 nodes, 7 buttons), but **4 of 7 buttons carry no label**. An
unlabelled control can only be addressed by tree position — the same weakness
that measured 50% and got Accessibility clicking dropped from production.

So everything now turns on one question: **is Accept labelled during an
incoming call?**

- If yes — calls are answered under allowlist control, which is the design.
- If no — the fallback is `AutoAcceptInvites`, which answers *everyone who
  calls*. That is a worse trade than it sounds, and I would rather ship
  FaceTime late than ship that as the default.

`AutoAcceptInvites` is **not** set on this Mac, deliberately.

To re-run either experiment, use Terminal — macOS attributes permissions to the
launching app, so they are `notDetermined` under anything else:

```bash
cd ~/Documents/MACMan
.venv/bin/python tests/tasks/call_audio.py       # transcription vs call quality
.venv/bin/python tests/tasks/degraded_intent.py  # do bad transcripts change actions
.venv/bin/python tests/tasks/facetime_probe.py   # is FaceTime's UI drivable
```

**Also found:** FaceTime ships no `.sdef`, so AppleScript is out entirely — it
is URL schemes plus Accessibility, which is why the fourth experiment is
load-bearing rather than a nicety.

**Done when:** you call your Mac from another room, ask for something out
loud, and it answers.

---

## Phase 3 — The app, and making it installable

The experience plan replaced the "setup web UI" idea with a native app, for a
reason that turned out to be security rather than polish: **a localhost UI could
not fix the permission problem, and a bundle could.** Granting Full Disk Access
to Terminal gives it to every script the user will ever run there; granting it
to `MACman.app` gives it to MACman alone.

Full detail in `private/EXPERIENCE_PLAN.md`.

| | Phase | Task | State |
|---|---|---|---|
| 🤖 | A | `egress.py` — one exit, described, authorised, recorded | ✅ 21/21 |
| 🤖 | B | Wire both senders through it; consent over text | ✅ |
| 🤖 | C | App skeleton: bundle, embedded Python, daemon as child, pipe IPC | ✅ 70 MB, self-contained |
| 🤖 | D | Native consent dialog | ✅ 17/17 |
| 🤖 | E | Settings: permissions, allowlist, engine, Keychain key | ✅ |
| 🤖 | F | Activity view — what ran, what left | ⬜ next |
| 🤖 | G | Setup wizard | ⬜ |
| 👤 | — | Create a self-signed **MACman Dev** certificate so permissions survive rebuilds | ⬜ |
| 👤 | — | Decide on $99 Apple signing once the app is real | ⬜ |
| 🤖 | H | Homebrew cask → `brew install --cask nikhilnmv/tap/macman` | ⬜ |
| 👤 | — | Create `Nikhilnmv/homebrew-tap`, tag a release | ⬜ |
| 🤖 | — | Fresh-machine test: wipe state, install as a stranger would | ⬜ |

**Verified so far:** Full Disk Access granted to `MACman.app` reaches the child
daemon; the daemon runs as a direct child of the app; the bundle runs entirely
from its own Python with nothing from the developer's environment.

**Done when:** someone who has never seen this can go from nothing to a working
answer in under ten minutes, without you helping.

---

## Phase 4 — Launch

| | Task |
|---|---|
| 🤖 | User docs: what it does, what it can't, what it will never do |
| 🤖 | Landing page live on GitHub Pages |
| 🤖 | Security page: threat model, what to check before trusting it |
| 👤 | Announce — Hacker News, r/macapps, Mac communities |
| 👤 | Decide what support burden you'll carry |

**Realistic outcome:** a few hundred stars, a handful of daily users, some
issues. That's a good result for an honest tool in a crowded space.

---

## Later, and honestly optional

| | Why it's not prioritised |
|---|---|
| Signed, notarized `.app` | $99/yr. Worth it **once people use this**, not before |
| Level 4 (vision, unscriptable apps) | Needs a funded API key; genuinely paid |
| More primitives | Each is hand-written; add them when a real user asks |
| AirDrop | No scriptable path; UI automation is 50% and sends the wrong file to the wrong person |

---

## What could kill this

| Risk | Honest assessment |
|---|---|
| **Nobody installs it** | Most likely outcome. Six permissions is a big ask from an unknown author |
| **A security incident** | Would be fatal to trust and could genuinely harm someone. Why phase 1 is first |
| **Apple or Anthropic ship it better** | Anthropic already has remote Mac control. Compete on privacy, not features |
| **You lose interest** | It's already useful to you. Phase 1 alone leaves something worth keeping |
| Apple changes an API | AppleScript is stable; Accessibility isn't, which is why we barely use it |

---

## How we work

- **Measure before believing.** Three assumptions about the on-device model
  were wrong this month; repeated trials caught each one. Single runs mislead.
- **Typed arguments, never composed strings.** 8/8 versus 1/5, and a security
  property as much as a reliability one.
- **Fail loudly at the boundary.** "That needs a key" beats a confident wrong
  answer, every time.
- **Publish the numbers that look bad.** The 50% accessibility result is why we
  don't click things. Hiding it would invite someone to re-propose it.
