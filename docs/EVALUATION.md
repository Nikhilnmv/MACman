# MACman — an honest evaluation

Scored as a product someone else might install, not as an amount of work done.

Written by the same agent that wrote most of the code, which is a real conflict
of interest and the reason every claim below points at something checkable.
Where the evidence is weak, that is stated rather than smoothed over.

**Date:** 20 August 2026 · **Version:** 0.1.0, unreleased

---

## The score

| Dimension | Score | Basis |
|---|---:|---|
| Engine and capabilities | **8/10** | 18 primitives, measured selection and correctness |
| Security architecture | **9/10** | One egress point, consent before anything leaves, 69 audit checks |
| Security completeness | **8/10** | Complete removal, credential coverage enforced structurally |
| Product coherence | **8/10** | The app now does the thing; status tells the truth |
| Documentation | **8/10** | Rewritten for the real product, links and UI strings verified |
| Code quality | **7/10** | No TODOs, annotated exception handling, two front ends that can drift |
| Distribution and updates | **4/10** | Tooling exists; nothing published, no automatic updates |
| **Real-user validation** | **2/10** | **Zero tasks have ever run through the app's text channel** |
| **Production readiness** | **6/10** | Good engineering, no external evidence |

The last two rows are the honest ones. Everything above them improved a lot;
neither of those moved, and they are what decides whether this is a product.

---

## What works well

**The engine is genuinely good.** 99% tool selection over 105 trials, 100%
answer correctness where checked against ground truth, multi-tool chaining
100% at two and three tools. Those came from repeated trials, not single runs,
and the method is in [RELIABILITY.md](RELIABILITY.md).

**The security design is better than most shipped software.** Not because the
ideas are novel, but because they are enforced structurally rather than by
discipline:

- Every byte bound for a cloud model passes one function, and an audit fails if
  any other module imports the Anthropic SDK or locates the `claude` CLI.
- `send` requires an authorisation receipt that only `authorise` issues, so
  forgetting to ask is a `TypeError` rather than a silent disclosure.
- Credential paths are refused in code with three independent checks, so a
  prompt injection that fully convinces the model still cannot read them.
- Permissions belong to `MACman.app`, not to Terminal — one auditable app the
  user can revoke on its own.

**The audits find real bugs, which is the only test of an audit.** Four
shipped-quality defects were caught by attacking rather than asserting:

| Bug | What it would have done |
|---|---|
| Case-insensitive path check | `~/.SSH/id_ed25519` returned live private key material |
| Consent reply sent as a string | `bool("false")` is `True` — every refusal recorded as approval |
| Revoke missed the Claude key | "Revoke everything" left a working, billable credential |
| Allowlist computed client-side | Editing it before the window loaded silently wiped it |

Two of those were found by an audit that *had already passed*, because it was
comparing the wrong thing. That is the pattern worth trusting here.

**The removal story is unusually complete.** `uninstall.sh` needs nothing but
macOS, so it works for someone who installed a release and never had a
checkout — and for someone who already dragged the app to the Trash. It covers
`~/Pictures/MACMan`, which is where screenshots sit and where nobody would look.

**The documentation is honest about limits**, including a threat model whose
"does not protect against" section is longer than its "protects against" one.

---

## What is incomplete

**Nobody has used it.** 961 tasks in the audit log, all from the CLI. **Zero on
the iMessage channel**, and the 8 authenticated sessions ever recorded were all
in August, before the app existed. The primary feature — text your Mac, it
answers — has never once been exercised through the product as it now ships.

**Nothing is published.** No git tag, no GitHub release, no cask in a tap. The
release tooling is built and verified end to end, but a user cannot install
this today by any means except cloning and building.

**No automatic updates, and no in-app check.** If a security fix ships it
reaches nobody who does not go looking. This is written down in
[CHANGELOG.md](CHANGELOG.md) rather than hidden, but writing it down does not
fix it.

**Unsigned.** `brew install --cask` quarantines, so the first thing a user
would learn is how to bypass Gatekeeper — for a tool that then asks for Full
Disk Access.

**FaceTime is not built.** Audio capture, transcription over call-quality audio
and spoken authentication are all proven. The call driver is not written, and
needs a second Apple device to develop against.

---

## What is fragile

**The on-device model is a shared system resource.** `LocalEngineUnavailable`
happens under memory pressure — observed once in an audit run, with the
identical task succeeding immediately after. MACman reports it rather than
guessing, which is right, but a user will read it as a fault.

**A persistent `chat.db` failure stops the poller until someone restarts it.**
Five transient failures are tolerated with backoff; beyond that it gives up and
reports why. That is deliberate — silently retrying a revoked permission
forever is worse — but there is no automatic recovery once the cause is fixed.

**Ad-hoc signing means macOS forgets permissions on every rebuild.** Harmless
for users of a signed release; corrosive during development, because it trains
you to click through permission dialogs without reading them.

**Two front ends can drift, and already have.** `appsettings`, `appactivity`
and `appsetup` are app-only, `macman setup` does not mention the app, and the
allowlist bug existed because the app computed state the daemon owned.

**Accessibility measured 50%** and is deliberately unused — but FaceTime would
need it, and four of seven buttons in FaceTime's window carry no label.

---

## What is confusing

**Which entry point is canonical?** `macman setup` and the app's wizard both
exist and do not reference each other. A user who runs the CLI setup gets no
hint the app exists.

**Two removal tools.** `revoke_all.py` revokes access; `uninstall.sh` removes
everything. The distinction is real and documented, but two scripts with
overlapping names is a thing to explain rather than something obvious.

**`CAPABILITY.md` and `COMMANDS.md` predate the app** and never mention it.
They are reference documents rather than instructions, so nothing in them is
false — but a reader moving between documents will notice the seam.

---

## What is unnecessary

**`route`, `run` and `repl` are development tools shipped to users.** They were
how the engine was tested before there was a product. They are harmless, and
they enlarge the surface a reader has to understand.

**`docs/index.html` describes a different product** and is linked from nothing.
It should be deleted rather than improved.

**`SHIPPING.md` is mostly superseded** by the roadmap. Its reasoning is worth
keeping; its sequence is not, and it now says so at the top.

**Screenshots are attached to replies by default** — `attach_screenshot = true`
— and there is **no toggle in Settings**. Turning it off means hand-editing
`config.toml`. For a tool whose pitch is that your data stays put, sending a
picture of your screen by default, with no visible control, is the wrong
default and the most substantive product criticism on this page.

---

## What still needs improvement

Ordered by how much each would change the answer to "is this a product".

1. **Use it.** Run [VERIFY.md](VERIFY.md) end to end from a phone, then use it
   for a week. Nothing else on this list is worth doing first, and no amount of
   further engineering substitutes for it.
2. **Run the clean-account test** ([FRESH_INSTALL.md](FRESH_INSTALL.md)). It is
   the only way to find the steps that only work because you built it.
3. **Decide signing**, informed by the Gatekeeper wording that test produces.
4. **Publish something**, so updates have a channel at all.
5. **Expose the screenshot setting**, and consider defaulting it off.
6. **Decide whether the CLI is a supported surface** or a development tool. It
   currently costs two implementations of everything.
7. **Get someone else to look at it.** One developer, one Mac, no external
   review — stated plainly in
   [SECURITY.md](SECURITY.md#9-a-review-by-anyone-but-its-author) and still true.

---

## How production-ready is it, really

**As a personal tool: ready.** It works, it is measured, it can be removed
cleanly, and its author understands its limits.

**As something to hand a stranger: not yet, and the gap is not code.** Every
remaining blocker is evidence, not engineering:

- Nobody has used the shipping product for its main purpose, once.
- Nobody but its author has read a line of it.
- It cannot be installed without a toolchain, or updated at all.

**6/10** is the honest number. The engineering underneath would score higher on
its own; the product is capped by having no external evidence that any of it
survives contact with a person who did not build it.

The most valuable next hour is not spent writing code. It is spent texting your
Mac from your phone and writing down everything that annoys you.
