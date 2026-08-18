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
| Accessibility navigation | **50%** — not used in production |
| **Outbound connections, private task** | **0** — audited, Python *and* Swift |
| **Adversarial attacks resisted** | **23/23** — after one real leak was found and fixed |
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
