# Reliability

Every measurement taken on this Mac, with the method, so claims can be checked
rather than trusted.

**Machine:** MacBook Air M2, 16 GB, macOS 26.3.1
**Local engine:** Apple `FoundationModels` (~3B, on-device)
**Cloud engine:** built but **never run** — no valid API key has been configured

All numbers are from repeated trials. Single runs are misleading here: this
model is non-deterministic, and one format measured 50% in one session and 17%
in another on identical inputs.

---

## Headline

| | |
|---|---|
| **Tool selection** | **99%** (104/105, 18 tools) |
| **Answer correctness, single tool** | **100%** (8/8) |
| **Multi-tool chaining** | **100%** (9/9 across 2- and 3-tool tasks) |
| **Transcription, worst call audio** | **9.8% WER** — and **0** change in action taken |
| Accessibility navigation | **50%** — not used in production |
| **Outbound connections, private task** | **0** — audited, Python *and* Swift |
| **Adversarial attacks resisted** | **23/23** — after one real leak was found and fixed |
| **Egress gate checks held** | **21/21** — nothing reaches a cloud model unasked |
| Shell command authoring | **20%** — not offered to this model |

**What MACman actually ships on:** typed tools with validated arguments. The
bottom two rows are why — both were measured, both failed, and both were
removed from the on-device path rather than shipped at those rates.

---

## Tool selection — 99% at 18 tools

25 tasks × 3 trials across every primitive. Tool calls are intercepted and
stubbed, so nothing executes; this measures *choice*, not effect.
(`tests/tasks/tool_selection.py`)

| Tool | Score |
|---|---|
| `count_files` | 6/6 |
| `list_folder` | 6/6 |
| `find_files` | 6/6 |
| `read_file` | 3/3 |
| `system_info` | 6/6 |
| `app_info` | 6/6 |
| `open_app` | 3/3 |
| `system_control` | 11/12 |
| `file_operation` | 6/6 |
| `media_control` | 6/6 |
| `browser_control` | 5/6 |
| `document_control` | 6/6 |
| `run_shortcut` | 3/3 |

### Adding four tools cost 6 points — but not to collisions

Mail, Calendar, Notes and Reminders took the set from 13 tools to 16, and
selection from **97% → 91%** (85/93). The failure pattern is not what tool
crowding looks like:

```
3×  reminders_control → (no tool called)
3×  system_control    → system_info
2×  mail_control      → (no tool called)
```

**Five of eight failures are "no tool called at all"** — the model answered
conversationally instead of acting. Both cases were *writes* phrased as casual
speech:

| Task | Score |
|---|---|
| "What reminders do I have outstanding?" | 3/3 |
| "Remind me to call the bank." | **0/3** |
| "How many unread emails do I have?" | 3/3 |
| "Draft an email to sam@example.com about lunch." | **1/3** |

Reads succeed, writes of the same tool fail. This is a *different* failure from
the Wi-Fi collision and merging would not touch it — there is no competing tool
being chosen, just no tool at all.

**The one real regression:** Wi-Fi status went 2/3 → 0/3, back to
`system_info`. Merging fixed the *control* case but not the *status* case.

### Both were fixed — and both were our bugs

**The write failures were a prompt defect, not a model limitation.**
`LOCAL_SYSTEM` said *"use your tools to answer anything **about** files,
folders, or **system state**"* — entirely read-oriented. Nothing told the model
to use tools to *do* things, so "remind me to call the bank" matched no
instruction and it replied conversationally. That was correct behaviour given
what it had been told. Adding an explicit "Doing things" section, naming
casual phrasings as real requests, fixed it:

| Task | Before | After |
|---|---|---|
| "Remind me to call the bank." | 0/3 | **3/3** |
| "Draft an email to sam@example.com about lunch." | 1/3 | **3/3** |

**Wi-Fi status was fixed by agreeing with the model.** It routed "is Wi-Fi
connected?" to `system_info` every time, across two different tool layouts.
Rather than fight it a third time, `wifi` was added as a `system_info` fact
alongside hostname, disk and battery — where the model was already looking.
Both tools now answer correctly. **Duplicating a capability is cheaper than a
wrong answer**, and cheaper still than another round of prompt archaeology.

### Full progression

| | Tools | Score |
|---|---|---|
| Baseline | 14 | 88% |
| Reworded descriptions | 14 | 89% |
| Merged `network_control` → `system_control` | 13 | 97% |
| Added Mail, Calendar, Notes, Reminders | 16 | 91% |
| Prompt fix + `wifi` fact | 16 | **100%** |
| Added VS Code + Claude Code (Level 3) | 18 | **99%** (104/105) |

**Adding tools does not degrade selection.** The 91% dip was two specific,
diagnosable defects, not crowding. Level 3's two tools scored 6/6 each on first
attempt with no tuning, and the single remaining miss is one trial of "what
song is playing" landing on `system_info` — noise at this sample size.

The practical ceiling on tool count is therefore not selection accuracy. It is
context length and the effort of writing each primitive well.

### How it got here: merging beats rewording

The first run scored **88%**, with two systematic failures:

```
6×  network_control  → system_control / system_info   "Is Wi-Fi connected?"
3×  document_control → app_info                       "Documents open in Pages?"
```

**Attempt 1 — rewrite the descriptions.** Led `network_control` with the words
people actually use ("Wi-Fi, internet, connected"), and added explicit negative
cross-references: `system_control` said *"NOT for Wi-Fi — use
network_control"*, `app_info` said *"NOT for Pages documents"*.

Result: **88% → 89%.** Inside this model's run-to-run noise, and an untouched
tool regressed a point. `"Turn Wi-Fi on"` stayed 0/3 despite the tool it kept
choosing explicitly disclaiming Wi-Fi.

**Conclusion: a ~3B model selects on surface semantic similarity and does not
act on exclusions.** Telling it *not* to pick something has no measurable
effect.

**Attempt 2 — delete the distinction.** `network_control` was merged into
`system_control`; Wi-Fi and Bluetooth are system settings in the way people
speak, so the separation was ours, not theirs.

Result: **89% → 97%.** Wi-Fi went 0/6 → 5/6. And `document_control` reached
**6/6 with no further change** — removing one competing tool resolved an
unrelated collision, because fewer tools crowd the same semantic space.

| | Baseline | Reworded | Merged |
|---|---|---|---|
| Overall | 88% | 89% | **97%** |
| Wi-Fi | 0/6 | 2/6 | **5/6** |
| `document_control` | 3/6 | 3/6 | **6/6** |

**Design rule:** when the model cannot separate two tools, merge them rather
than arguing with it. Rewording bought ~1 point; merging bought 9. Prefer a
smaller tool set with wider action enums — the model picks enum values
reliably (8/8) and distinguishes similar tool names poorly.

## Answer correctness — 100% where measured

Typed tools, checked against ground truth (`tests/tasks/suite.py --run local`):

| Question | Answer | Truth |
|---|---|---|
| PDFs in Downloads | 25 | 25 ✅ |
| Files in Downloads | 249 | 249 ✅ |
| Files matching "invoice" | 1 | 1 ✅ |
| macOS version | 26.3.1 | 26.3.1 ✅ |
| Hostname | *(exact match)* | ✅ |

Multi-tool chaining held at every size tested: **2 tools 3/3, 3 tools 3/3**,
including combining results into one answer. An earlier assumption that a 3B
model could not chain tools was simply wrong.

---

## Why typed tools, in numbers

The single most consequential design decision, and it was measured rather than
assumed:

| Approach | Correct |
|---|---|
| Model writes shell commands | **1/5 (20%)** |
| Model fills typed fields | **8/8 (100%)** |

Given a raw shell tool it produced, in three consecutive runs:

```
ls -l ~/Downloads | grep -v . | grep Pdf | wc -l    # deletes every non-empty line
ls -1 Downloads | grep 'PDF'                         # relative path → exit 1
df -h /Users/me/Downloads                            # wrong command, invented path
```

…then reported confident file counts from all three, including the one that
exited non-zero. **It is reliable at choosing and unreliable at authoring
syntax**, and every design rule follows from that.

---

## Accessibility navigation — 50%, not shipped

Whether the model can pick a UI element out of an accessibility tree
(`tests/tasks/ax_navigation.py`, `ax_label_vs_path.py`):

| Format | Correct |
|---|---|
| Reply with the element's **path** | 8/16 (50%) · 2/12 in a later session |
| Reply with the element's **label** | 6/12 (50%) |
| Label + parent hint | 2/12 (17%) |

**Tree size is not the variable** — a 5-node dialog failed while a 21-node
Settings pane passed. The failure is in *constructing the path*: it produced
`0/4/1` for `1/4/1` (right leaf, wrong root) and `3/3` for `2` in a flat tree
(inventing nesting that did not exist).

**Labels beat paths roughly 3× within a single session** (50% vs 17%), so if
Accessibility is ever used, elements must be addressed by label.

**Why it is not shipped:** a wrong click is not a wrong answer — it presses
something. One trial chose "Cancel" where the answer was "Don't Save". This is
why AirDrop is deferred and why Level 2 prefers AppleScript, Shortcuts and URL
schemes over UI automation.

---

## Call audio — the prediction was wrong

The plan said transcription would fail on compressed call audio, and named it
the experiment most likely to kill voice-over-FaceTime. It didn't fail.

Method: macOS `say` produces speech with an exact known transcript — the ground
truth a microphone test can never have — which is then degraded and transcribed
by the same on-device recogniser MACman uses. Encoding is **AAC-ELD via
`afconvert`**, the codec FaceTime actually uses, not a stand-in.

| Condition | Mean WER | Exact |
|---|---|---|
| Clean 48 kHz reference | 0.0% | 6/6 |
| AAC-ELD 24 kbps (codec only) | 0.0% | 6/6 |
| Narrowband 8 kHz | 0.0% | 6/6 |
| Background noise, 20 dB SNR | 0.0% | 6/6 |
| Background noise, 10 dB SNR | 0.0% | 6/6 |
| Background noise, 5 dB SNR | 1.9% | 5/6 |
| 2% packet loss | 0.0% | 6/6 |
| 5% packet loss | 0.0% | 6/6 |
| 10% packet loss | 0.0% | 6/6 |
| Bad call (10 dB + 5% loss) | 1.9% | 5/6 |
| **Terrible call** (5 dB + 10% loss, 8 kHz, 16 kbps) | **9.8%** | 2/6 |

**The codec is a non-issue, and so is packet loss.** 10% of 20 ms packets
dropped cost nothing measurable; speech is redundant enough to absorb it. Only
background noise below about 10 dB SNR degrades anything.

### Two wrong versions came first

**Version 1 varied only bitrate and sample rate, and scored 0.0% everywhere.**
That was nearly recorded as a pass. It isn't one: when the hardest condition
scores exactly like the easiest, the test has not shown robustness, it has
shown that nothing in it was hard enough to discriminate. It also varied the
least damaging property of a call.

**Version 2 added noise and loss, and inverted itself.** "Terrible call" scored
*better* than plain 5 dB noise. Adding damage cannot improve accuracy, and that
contradiction exposed a real bug: noise was added at 48 kHz before resampling,
so its energy spread to 24 kHz and resampling discarded most of it. Measured
here, a nominal 5 dB SNR arrived as **10.5 dB at 16 kHz and 13.7 dB at 8 kHz** —
labels wrong by up to 9 dB, and the harshest-looking condition was the mildest.

Fixed by resampling first and adding noise in-band. The experiment now
calibrates itself before scoring anything and refuses to be trusted if delivered
SNR drifts more than 1 dB from the label.

## What the errors actually cost — nothing

WER is the wrong measure here. The errors bad audio produces are
`Downloads → download`, `Documents → document`, `emails → mails`, and a dropped
`please`. They cost WER points without changing what the user wants.

The failure that would matter is subtler: right tool, **wrong argument**.
`folder="download"` does not exist, so MACman would answer "download does not
exist or is not a folder" — a precise, confident, useless reply to a question
the user asked correctly.

Measured by replaying the real degraded transcripts through the engine
(`tests/tasks/degraded_intent.py`), scoring tool *and* arguments:

| | Correct tool | Correct action |
|---|---|---|
| Clean transcript | 16/18 | 16/18 |
| Degraded transcript | 17/18 | 17/18 |

**Mis-transcription changed nothing.** The argument stayed correct every time —
`download folder` still produced `folder="Downloads"`. The degraded score is
one trial *higher*, which is noise at this sample size, not an effect.

Both misses are the same request, "Open Safari and check the battery level",
which asks for two things at once and splits between `open_app` and
`system_info` on clean audio too. It measures ambiguity, not audio damage, and
is kept rather than removed because dropping it would flatter the result.

**Sample is small** — 18 trials over 6 requests. Enough to rule out a large
effect, not to detect a small one. And synthesised speech has no accent, no
hesitation and perfect articulation, so all of this remains an optimistic bound
until a real call is tested.

## A leak found by reading, not by running

Not every finding comes from a benchmark. This one came from tracing what a
TOTP code looks like when it arrives by voice instead of by text, before the
FaceTime channel existed to expose it.

Typed, a code arrives as `482913`. Spoken, it arrives as **"four eight two nine
one three"** — no digits at all. Two things then go wrong:

* `security.auth.verify` reduces its argument to digits, so a worded code
  reduces to the empty string and fails. Five of those locks the caller out for
  fifteen minutes, having said the right thing every time.
* `session._looks_like_code` counts digits to decide whether a message is
  *only* a code. A worded code contains none, so it is treated as a **task and
  handed to an engine as text** — the credential-to-model leak already fixed
  once for iMessage, arriving again by a different road.

`voice/digits.py` converts spoken numbers, and is strict in the same direction
as the function it protects: a code is recognised only when the whole utterance
is nothing but a number, so `delete file 123456` stays a task.

**The subtle part was arithmetic.** "forty eight" is 48, not 40 followed by 8.
Parsed naively, a code grouped in pairs produced eight digits, failed the
length check, and was forwarded — a security failure caused by a carry.

| | |
|---|---|
| Spoken and typed forms covered | **27/27** |
| Typed-path regressions | **0** |

Tested in both directions on purpose: a code missed becomes a leak, and a task
mistaken for a code silently does nothing.

### Number transcription is locale-dependent

Speaking the codes aloud and transcribing them found something guessing would
not have. Of the three ways a code can be spoken, two return digits directly —
read out digit by digit gives `482913`, grouped in pairs gives `48 2913`. Read
as a cardinal number, on a Mac set to an Indian region, it returns:

> `Four lakh 82,913`

**What a recogniser does with numbers depends on the region the Mac is set
to.** `lakh` and `crore` are now parsed, and thousands separators are stripped
so `82,913` does not split into 82 and 913 — the same class of silent value
change as the "forty eight" carry.

Magnitudes are parsed as a *separate reading* rather than folded into the
digit-run logic, because the two conflict: "four eight two" is the digits
4, 8, 2, while "four hundred" is the single value 400. The cardinal path runs
only when a magnitude word is present, so `transfer four hundred thousand to
savings` stays a task.

```bash
.venv/bin/python tests/tasks/spoken_code.py           # strings, no permissions
.venv/bin/python tests/tasks/spoken_code.py --audio   # what the recogniser emits
```

## The one exit — 21/21

Every byte bound for a cloud model passes through `security/egress.py`, and the
audit attacks that gate rather than asserting it (`tests/audit/egress.py`).

| Attack class | Checks | Held |
|---|---|---|
| The consent gate itself | 5 | 5 |
| Pre-approval scoping | 7 | 7 |
| Receipt reuse | 2 | 2 |
| Bypassing the gate entirely | 1 | 1 |
| Wiring — do the senders really refuse? | 6 | 6 |

The checks worth naming, because each encodes a mistake that would otherwise be
easy to make:

* **`~/projects` must not cover `~/projects-secret`.** String prefixes match
  unrelated siblings; the check compares path components and identity.
* **`~/Projects` must cover `~/projects`.** macOS is case-insensitive, so a
  rule that stops working when the user capitalises differently is a bug in the
  other direction.
* **A receipt cannot be reused for different data.** Approving *this* is not
  approving *something like it*.
* **An expired pre-approval is dead**, and a task with no path matches no
  directory-scoped rule.

**The wiring checks drive the real code paths** with an asker that always
refuses, rather than grepping for the word `egress` — an import that is never
called would pass a grep. Two of them exist to stop the disclosure becoming
dishonest over time: one fails if the cloud disclosure is ever marked `EXACT`
when it cannot be, and one fails if `excluded` grows to name folders MACman
cannot actually guarantee.

## Attacked, not asserted — 23/23

Every security property here was a *claim* until it was attacked
(`tests/audit/injection.py`). The threat model: someone who can put text in
front of MACman — a file you ask it to read, an email, a filename — but cannot
run code on your Mac. If they can run code, MACman is not the problem.

| Attack class | Vectors | Held |
|---|---|---|
| Reaching `~/.ssh` by indirection | 10 | 10 |
| Hostile instructions inside file content | 5 | 5 |
| Hostile instructions in a *filename* | 1 | 1 |
| Talking past the confirmation gate | 3 | 3 |
| Forcing a private task to the cloud | 4 | 4 |

### It found a real leak, and the audit itself was wrong first

The first run reported **21/21 held** — and was wrong. One "held" row carried
the note `resolved to /Users/…/.SSH/id_rsa` rather than "refused", which is not
what a refusal looks like.

**macOS filesystems are case-insensitive by default.** `~/.SSH/id_ed25519` and
`~/.Ssh/id_ed25519` open the same file as `~/.ssh/id_ed25519`. The path check
compared strings, so a different spelling of the same file passed it. Both
variants **returned real private key material** — on the typed-tool path and
the shell-guard path alike.

The audit had scored those attacks as held because it, too, compared strings.
A test that shares its subject's bug confirms the bug.

Both were fixed. `typed._within()` now applies three independent checks —
resolved prefix (catches `..` and symlinks), case-folded prefix (catches
spelling), and inode identity via `samefile` (catches hard links and
firmlinks) — and the guard case-folds. The audit now scores path attacks by
`samefile`, not by spelling, because identity on disk is the only thing that
decides whether a file was reached.

**The lesson worth keeping:** a security test that passes proves nothing until
you have watched it fail. This one passed for the wrong reason while a private
key was readable.

```bash
.venv/bin/python tests/audit/injection.py   # any PASS means an attack succeeded
```

---

## Nothing leaves the Mac — audited

The central privacy claim, measured rather than asserted
(`tests/audit/network.py`). Every socket connection MACman's Python process
attempts is recorded while real private tasks run end to end.

| | Outbound connections |
|---|---|
| Routing decisions (3 sensitive tasks) | **0** |
| Private tasks through the local engine (5 tasks) | **0** |

Tasks covered: Downloads contents, Documents contents, unread mail count, note
count, outstanding reminders — all answered correctly, all offline.

Re-run it yourself:

```bash
.venv/bin/python tests/audit/network.py
```

### The Swift helpers — the gap that check couldn't see

A Python-level socket patch cannot observe a separate process, so the helpers
doing the two most privacy-sensitive jobs — running the model and transcribing
speech — were outside it. Apple documents `FoundationModels` and
`SFSpeechRecognizer` as on-device, but documented is not verified, and this is
precisely the claim the whole product rests on. So it was checked directly.

Static linkage first: `otool -L` shows no networking framework linked in any of
the five helpers. **Not conclusive on its own** — `URLSession` lives inside
`Foundation`, which every one of them links — so each was also observed at
runtime while doing real work.

| Helper | Observed during | Open sockets |
|---|---|---|
| `macman-local` | a real inference answering a file question | **none** |
| `macman-speech` | recogniser start-up and audio-engine capture | **none** |

**The limit of the speech result:** the recogniser initialised and the audio
engine ran, but no words were spoken during the observation, so this shows
*setup* opens no connection. A full transcription of real speech would be the
stronger claim and has not been made. The inference result carries no such
caveat — that was a complete, correct answer produced with no socket open.

## Dependency surface — audited

Every Python package inherits whatever permissions MACman holds, up to Full
Disk Access, so the install list is security surface rather than a convenience
question.

| | Packages |
|---|---|
| Free, on-device tier | **11** |
| With the Claude tier (`[cloud]`) | 35 |

**The finding:** the free tier was importing the Anthropic SDK purely for a
`@beta_tool` decorator that turns a docstring into a JSON schema. That single
import dragged in up to 40 transitive packages — `boto3`, `botocore`,
`aiohttp` among them — into a tool whose entire pitch is running offline with
no API key.

Replaced with a 150-line local decorator (`agent/tools/schema.py`) producing
byte-identical schemas. Verified by blocking `anthropic` at import and
confirming all 18 tools, the local engine and the router still load.

`anthropic` is now an optional extra, needed only by the cloud engine.

**A correction:** this table read **17** until the count was actually measured.
That number was carried over from the change that removed the SDK and never
re-checked; a clean install of the declared dependencies produces 11. Two
packages sitting in the development environment (`CoreServices`, `FSEvents`)
are orphans from an earlier dependency set and belong to no tier. The real
number is better than the one being advertised, which is exactly why it went
unquestioned for as long as it did.

### The app bundle

`MACman.app` carries its own Python, the daemon and the Swift helpers, so it
depends on nothing in the user's environment. macOS ships Python 3.9 and the
daemon needs 3.11 for `tomllib`; depending on a Homebrew Python would break the
app the day the user upgrades it.

| | |
|---|---|
| Runtime, as downloaded | 108 MB |
| After trimming | **68 MB** |
| Bundle total | **70 MB** |
| Modules verified importable from the bundle | **34/34** |

The two largest savings were not the obvious ones. **`PyObjCTest` is 16 MB** —
pyobjc ships its own test suite inside the wheel — and **`pip` is 11 MB**, with
no use once the bundle is built. Tcl/Tk, IDLE and the C headers followed.

Trimming stopped at whole directories that no import path reaches. Going
module-by-module through the stdlib saves a few more megabytes and risks an
`ImportError` that appears on someone else's Mac months later, which is a poor
trade for a tool asking to be trusted.

Because that trim list is hand-written, **the build imports all 34 modules the
daemon can reach** from the bundled interpreter with the repository's
`site-packages` excluded. A bad trim fails the build rather than shipping.

The runtime is pinned by URL and SHA256 and refuses to embed on a mismatch: it
runs with whatever permissions the user grants MACman, so it is the same class
of supply-chain surface as the packages below.

> **A bug worth recording:** the first attempt to pin a runtime selected the
> *freethreaded* (no-GIL) build, because the code took `sorted(...)[0]` and
> "freethreaded" sorts first. pyobjc publishes no freethreaded wheels, so the
> dependency install would have failed after a 30 MB download for a reason
> nothing in the output explained. Selection is now an exact match that refuses
> unless it finds exactly one candidate.

### Pinned by hash

`requirements.lock` pins all 11 to exact versions with every sha256 PyPI
publishes for them, generated from `pyproject.toml` by `scripts/lock_deps.py`
so the list cannot drift from what is declared. Each package carries a one-line
justification, and the generator exits non-zero if any lacks one.

| Check | Result |
|---|---|
| Clean install, `--require-hashes` | 11 packages, no failures |
| All digests for one package corrupted | **refused, nothing installed** |

The second row is the one that matters: a lock that is never tested against a
bad artifact is decoration. An initial attempt at that test corrupted only one
of two digests and pip installed anyway — correctly, by falling back to the
sdist whose hash still matched. The test was wrong, not the defence.

```bash
pip install --require-hashes -r requirements.lock
```

## Model quirks worth knowing

**Spurious refusals — ~1 in 4, text-only.** Benign requests declined with
invented justifications ("phishing", "misleading"). Every tool-using trial has
passed, suggesting the rate is much lower when reporting facts rather than
generating claims — a hint, not a measurement.

**Claude's system prompt is actively harmful here.** Reusing it dropped tool
use from 4/4 to 1/4; its security vocabulary ("prompt injection",
"credentials", "refused") primes Apple's safety layer to decline ordinary
requests. Apple's model has its own prompt for this reason.

**Context is 4096 tokens by default and evicts oldest-first.** A 257-file
listing pushed the user's question out of the window, leaving the model
answering from a bare file list. Fixed by raising `num_ctx` and capping tool
output.

**Every field must be Optional or omission is fatal.** `@Generable` requires
all fields; the model correctly omitting an unused one raised
`decodingFailure`. Several tools were passing on luck until this was found.

**A plain `swift build` silently disables every tool.** The helper only gets
tool support when built with `-DMACMAN_TOOLS`, which needs full Xcode. Without
it the binary compiles, runs, reports itself available — and calls no tools at
all. Rebuilding without the flag took tool selection from **99% to 0%**, with
no error anywhere; the only symptom was MACman becoming vague.

Worse, it silently invalidated a benchmark. An experiment comparing two
conditions scored 0/18 on both and concluded "mis-transcription did not
meaningfully change what happened" — tidy, confident and entirely false, since
a difference between two broken runs is zero. **A broken control reads as
reassurance, which is worse than an error.** `preflight` now reports engine
tool status, and the benchmarks refuse to run without it.

**A permission is attributed to the app that launched the process, not to
MACman.** Running the same binary from Terminal and from another app gives
different answers: `authorized` in one, `notDetermined` in the other. Worse,
*requesting* a permission from a launching app that declares no usage
description makes macOS **kill the process with SIGABRT** — no prompt, no
error, nothing catchable.

This was found when file transcription crashed instead of failing. It only
occurs while a permission is `notDetermined`, so it is invisible on a Mac
where the permission was already granted — which was every Mac this project
had run on. **A fresh install would have crashed where it should have asked.**
Requesting is now isolated in a separate `authorise` command meant for setup;
`listen` and `transcribe` report and exit rather than prompting.

**The on-device model is sometimes simply unavailable.** One task in an audit
run failed with `LocalEngineUnavailable` and the identical task succeeded on
the next run, with no change in between. Apple's model is a shared system
resource and can decline under memory pressure or while another process holds
it. This is recorded rather than re-run away because **users will hit it**: it
looks like a bug in MACman and is not one. MACman reports it as a failure
instead of guessing an answer, which is the intended behaviour.

---

## Not yet measured

| | |
|---|---|
| **Claude engine** — cost, latency, accuracy | Never run; no key configured |
| Long chains (5–10 tools) | Only up to 3 tested |
| Refusal rate *with* tools | Only text-only measured |
| Real accessibility trees | Experiments used hand-modelled trees |
| Selection accuracy past 18 tools | 99% at 18 is the baseline to re-run |
| Sockets during a *full* transcription | Only recogniser start-up observed |
| Transcription accuracy on call audio | Clean-mic only; compressed audio untested |
| Anything on a Mac that is not this one | Single machine, single user |

---

## Reproducing

```bash
.venv/bin/python tests/tasks/tool_selection.py --trials 3   # selection, nothing executed
.venv/bin/python tests/tasks/suite.py --routing             # routing, free
.venv/bin/python tests/tasks/suite.py --run local           # correctness vs ground truth
.venv/bin/python tests/tasks/ax_navigation.py               # accessibility navigation
.venv/bin/python tests/audit/injection.py                   # adversarial, offline
.venv/bin/python tests/audit/network.py                     # outbound connections
.venv/bin/python scripts/lock_deps.py > requirements.lock   # regenerate the lock
```
