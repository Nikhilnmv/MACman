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
Local voice                 ████████████████░░░░  works; unverified by you
FaceTime calling            ████░░░░░░░░░░░░░░░░  audio proven, call driver not built
Ready for strangers         ███░░░░░░░░░░░░░░░░░  private repo, 4-command install
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

### 1b. Adversarial security testing

The defences are asserted, not attacked. That has to change before anyone else
installs this.

| | Task |
|---|---|
| 🤖 | **Prompt injection suite** — malicious text in files, web pages, emails, filenames, attempting exfiltration and destructive actions |
| 🤖 | Attempt to reach `~/.ssh` by indirection, symlink, relative path, unicode |
| 🤖 | Attempt to talk past a confirmation gate |
| 🤖 | Attempt to make a private task route to the cloud |
| 🤖 | Dependency audit: pin all 17 with hashes, justify each |
| 🤖 | Swift helper network check (the Python audit can't see their sockets) |
| 🤖 | Write the threat model honestly — including what MACman does **not** protect against |

**Done when:** an attacker with the ability to put text in front of MACman
cannot make it exfiltrate data, destroy files, or bypass a gate — and where
that isn't true, it's documented rather than hidden.

---

## Phase 2 — FaceTime *(needs your iPad)*

Co-priority with texting, and the thing you originally set out to build.

**Already proven:** Core Audio process taps capture one app's audio without
touching the rest of the system — verified, 1.4 MB captured at 48kHz.
On-device transcription is perfect on clean speech. Speaking into BlackHole
works, speakers untouched.

| | Task |
|---|---|
| 👤 | **Second Apple device** — the hard blocker; nothing here works without it |
| 🤖 | `channels/facetime` — place and answer calls, verified state per step |
| 🤖 | Wire tap → transcription → engine → BlackHole into a live call loop |
| 🤖 | Barge-in: stop talking when interrupted |
| 🤖 | Wake phrase + code over voice — a network caller is unverified, unlike someone at the keyboard |

**Four experiments, each with a fallback:**

| Question | If it fails |
|---|---|
| Does `AutoAcceptInvites` still work on macOS 26? | click Accept via Accessibility |
| Do taps capture FaceTime specifically? | BlackHole as system output, worse but works |
| **Is transcription accurate on compressed call audio?** | cloud speech-to-text; moves voice to the paid tier |
| Is FaceTime's Accessibility tree drivable? | URL schemes only; fewer controls |

The third is the one I'd bet against. Your clean-mic test was perfect; a
FaceTime call is a different signal entirely.

**Done when:** you call your Mac from another room, ask for something out
loud, and it answers.

---

## Phase 3 — Make it installable

Right now: private repo, Xcode required, four commands, six permissions. No
stranger gets through that.

| | Task |
|---|---|
| 👤 | **Make the repo public** — blocks Homebrew and GitHub Pages both |
| 🤖 | **Write the user journey first** — install → first task → daily use, as prose, before building any of it |
| 🤖 | Setup web UI: permissions with live status, allowlist, wake phrase, QR code, self-test |
| 🤖 | Homebrew tap → `brew install nikhilnmv/tap/macman` |
| 👤 | Create `Nikhilnmv/homebrew-tap`, tag a release |
| 🤖 | Fresh-machine test: wipe state, install as a stranger would, record every friction point |

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
