# Changelog

What changed in each release, and why it mattered.

Security fixes are listed first in every entry and marked **Security**, because
someone deciding whether an update is urgent should not have to read a feature
list to find out.

---

## 0.1.0 — unreleased

First release. Everything below is what exists, not what is planned.

### What it does

Text your Mac from your phone and it answers. 18 capabilities across files,
system control, Mail, Calendar, Notes, Reminders, browsers, media and developer
tools. The everyday half runs on Apple's on-device model: free, offline, and
nothing leaves the machine.

- **99% tool selection** over 105 trials, 100% answer correctness where checked
  against ground truth
- **0 outbound connections** during private tasks, audited in Python and by
  observing the Swift helpers directly
- `MACman.app` — menu bar, setup wizard, settings, activity log
- Voice mode, on-device transcription and speech

### Security

- **One exit for data.** Everything bound for a cloud model passes through
  `security/egress.py`, which describes exactly what would leave, asks, and
  records the answer. Refusing is the default when there is nobody to ask.
- **Permissions belong to MACman, not Terminal.** The daemon runs as a child of
  the app, so Full Disk Access covers one auditable app rather than every
  script you will ever run in a shell.
- **Credential paths refused in code** — `~/.ssh`, `~/.aws`, `~/.gnupg`,
  Keychains — so a successful prompt injection still cannot read them.
- **84 audit checks** across four suites, every one of them attacking a defence
  rather than asserting it.

Four real bugs were found by those audits during development, each of which
would have shipped:

| Bug | Consequence |
|---|---|
| Case-insensitive path check | `~/.SSH/id_ed25519` returned live private key material |
| Consent reply sent as a string | `bool("false")` is `True` — every refusal recorded as approval |
| Revoke missed the Claude key | "Revoke everything" left a working, billable credential |
| Allowlist written client-side | Editing it before the window loaded silently wiped it |

### Known limits

- **No automatic updates.** See [Updating](#updating) below.
- Not signed with a Developer ID, so `brew install --cask` will show a
  Gatekeeper warning until that changes.
- FaceTime calling is not built. Audio capture and voice authentication are
  proven; the call driver needs a second Apple device to develop against.
- Reviewed by one person, on one Mac. See
  [SECURITY.md](SECURITY.md#9-a-review-by-anyone-but-its-author).

---

## Updating

**There is no automatic update mechanism, and no in-app check.** That is a
deliberate gap rather than an oversight, and it has a cost worth stating: **if a
security fix ships, it reaches nobody who does not go looking.**

Once a release exists:

```bash
brew upgrade --cask macman
```

Until then, from a source checkout:

```bash
git pull
cd helpers && swift build -c release -Xswiftc -DMACMAN_TOOLS && cd ..
cd app && ./build.sh --embed && cd ..
```

Your settings, login code and activity log live outside the app and survive an
update untouched.

**To hear about security fixes**, watch the repository on GitHub — releases
tagged **Security** are the ones that matter. An in-app updater would be
better, but it wants a signed app and adds an update channel to a tool holding
Full Disk Access, which is a trade to make deliberately rather than by default.
