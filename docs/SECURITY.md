# Security

MACman is a program that acts on your Mac when someone sends it a text message.
There is no framing of that sentence which makes it a small thing to install.

This document states what it defends against, what it does **not** defend
against, and how to check both yourself. The second list is longer than the
first, and that is the honest shape of the problem — not a disclaimer.

**If you read one section, read [What MACman does not protect
you from](#what-macman-does-not-protect-you-from).**

---

## The permissions it holds

MACman asks for capabilities, not a blanket grant — each one is optional, and
refusing one disables the features that need it rather than the program. But
you should know what the big one means.

| Permission | What it grants | If MACman were malicious |
|---|---|---|
| **Full Disk Access** | Read `chat.db` for iMessage, and read files anywhere | It could read every file your user account can read |
| Automation | Drive Mail, Calendar, Notes, Reminders, browsers | It could send mail as you, read your calendar |
| Accessibility | Query window state, detect lock state | It could observe app UI |
| Screen Recording | Screenshots | It could watch your screen |
| Microphone | Voice mode | It could listen |

**Full Disk Access is the one that matters.** iMessage's database is protected
by it, so the text channel cannot work without it. That is not a design choice
we can engineer away — it is where Apple put the data.

The consequence: **every one of the 11 Python packages MACman installs runs
with those permissions too.** That is why the dependency list is pinned by hash
and justified line by line (`requirements.lock`), why the embedded Python
runtime is pinned by SHA256 as well, and why the free tier deliberately has no
cloud SDK in it.

### You grant them to MACman, not to your Terminal

This changed, and it is the single largest security improvement the project has
made.

macOS attributes a permission to the **responsible process** — the app that
launched the one asking. Running the daemon from a terminal meant Full Disk
Access had to be granted to **Terminal itself**, which does not give it to
MACman: it gives it to *every script you will ever run in a shell*, forever,
including ones you have not written yet.

`MACman.app` now spawns the daemon as a child, so the grant belongs to MACman
alone. It appears under its own name in System Settings, and revoking it there
affects nothing else. Verified on this Mac: Full Disk Access granted to the app
reaches the child daemon, and the daemon runs with the app as its direct parent.

A LaunchAgent would undo this — the responsible process becomes `launchd` and
the permission attaches to a bare binary with no bundle — which is why the
daemon must stay a child, and why **quitting the app stops MACman**.

Check what is granted, and turn any of it off:

```bash
.venv/bin/python -m macman.main preflight
```

---

## What MACman defends against

Each of these is tested in `tests/audit/injection.py`, which **attacks** the
defence rather than asserting it. 23 attack vectors, 23 resisted — as of the
last run, on one Mac.

### It can lock your Mac. It can never unlock it.

Not a policy — a property of macOS. **Secure Input** blocks synthetic keystrokes
at the lock screen, so no program running as your user can type your password
there. MACman also holds no password: it is never requested, transmitted or
stored, and there is no code path that would accept one.

**The worst case for a stolen session is a *more* locked Mac.**

### Your login password is not the credential

Sessions authenticate with a **TOTP** code from your authenticator app. The
secret lives in the macOS Keychain, never on disk in the clear. Codes are
single-use within their window — a code observed over your shoulder or scraped
from a screenshot cannot be replayed. Five failures trigger a 15-minute lockout.

You can revoke MACman's access without changing anything about your Mac.

### Unknown senders never reach a model

The allowlist is checked **first**, before any engine sees the message text
(`session.py`: allowlist → kill switch → auth → route → engine). A message from
a handle you did not list is dropped in silence — no reply, no log of content,
no model invocation. An attacker who does not know your allowlisted handle
cannot even make MACman *think*.

### Credential paths are refused in code, not by prompting

`~/.ssh`, `~/.aws`, `~/.gnupg` and `~/Library/Keychains` are denied by a check
in Python, at the point a path is used. A successful prompt injection — a model
fully convinced it should read your private key — still cannot, because the
refusal does not depend on the model's cooperation.

This check applies three independent tests, because two of them were once
enough and were not:

1. **Resolved prefix** — catches `..` traversal and symlinks
2. **Case-folded prefix** — macOS is case-insensitive; `~/.SSH` is `~/.ssh`
3. **Inode identity** (`samefile`) — catches hard links and firmlinks

> **This was a real vulnerability, not a hypothetical.** Until it was found,
> `~/.SSH/id_ed25519` returned real private key material, and the audit scored
> that attack as *passing* because it compared spellings too. See
> [RELIABILITY.md](RELIABILITY.md#it-found-a-real-leak-and-the-audit-itself-was-wrong-first).
> Assume more bugs of this shape exist.

### Reading text is never executing it

Hostile instructions inside a file, an email, or even a *filename* come back as
data. Tested with fake system turns, forged authority claims, urgency framing,
and payloads shaped like tool calls. The structural reason this holds: the
on-device model gets **typed tools**, never a shell. Python builds every command
from validated arguments, so a malformed or injected command is impossible to
express rather than merely discouraged.

### Private content routes to the on-device model

The router decides local-vs-cloud and **never makes a network call** to do it —
asking a cloud model "is this private?" has already leaked the filename. Tested
against attempts to override it with claimed authorisation and developer framing.

### Destructive actions ask, every time

Deleting goes to the Trash, not oblivion. With no session to ask, guarded
actions fail closed rather than proceeding.

### Everything is logged

Every tool call is appended to `~/Library/Application Support/MACman/audit.jsonl`
before it runs. Tool *results* are stored as a hash, not content, so the log is
safe to read without re-exposing what the tool touched.

---

## What MACman does not protect you from

### 1. Anyone who controls a handle on your allowlist

**This is the most realistic attack, and MACman does not solve it.** The
allowlist trusts a phone number or Apple ID. A SIM swap, a stolen and unlocked
iPhone, or a compromised Apple ID means an attacker sends messages MACman
accepts as yours.

TOTP is the reason this is not immediately fatal — they still need a live code.
But **if the same stolen phone holds your authenticator app, they have both
factors.** Keep them on different devices if that matters to you.

### 2. Anyone with physical access to your unlocked Mac

They do not need MACman; they have a Mac. It changes nothing here, for better
or worse.

### 3. A compromised dependency

Hash pinning stops someone substituting a *different artifact* for a version we
recorded. It does **not** stop a maintainer's account being compromised and a
malicious new version published, which we would then pin on the next update.
Eleven packages hold Full Disk Access alongside MACman.

### 4. The confirmation gate, against a determined attacker

The `CONFIRM` list is regex-based, and any pattern list can be obfuscated
around. **It exists to catch a mistaken agent, not an adversarial one.** The
real boundaries are the coded `DENY` paths, the absence of a shell on the local
engine, and the fact that MACman runs as your user and cannot escalate to root.

Do not treat "it asks before deleting" as a sandbox. It is not one.

### 5. Anything the person holding your session asks for

MACman is not a policy engine. An authenticated user can ask it to trash files,
send mail, or read documents — that is the product. It will ask once for
destructive things and then comply. There is no second opinion.

### 6. The cloud tier, if you enable it

If you install `[cloud]`, configure a key, and approve a task, **that content
goes to Anthropic** under their terms. The router tries to keep personal things
local and measured 20/20 doing so — but that is a heuristic over phrasings we
thought of. A wording nobody anticipated could route somewhere you did not
intend.

Since the egress gate, that risk is bounded rather than merely mitigated:
**nothing reaches a cloud model without a disclosure you approved**, so a
mis-routed task becomes a question rather than a leak. What the gate cannot do
is make the disclosure smarter than the code producing it.

Two honest limits worth knowing:

* **A cloud task can still read your files.** The disclosure says so — it warns
  that whatever Claude looks up is sent too — but "the request" is not the
  whole payload. A `bash` call that reads a document sends that document.
* **Claude Code is not governed by MACman at all.** Handing a task to the
  `claude` CLI starts a separate program running with your account's access.
  MACman's credential blocks, guard and audit log apply to *MACman's* tools,
  not to it. The disclosure states this outright rather than implying
  protection that does not exist.

The free tier has no cloud SDK installed at all, which remains a stronger
guarantee than any gate or routing rule.

### 7. Other software on your Mac

The audit log, staged attachments in `~/Pictures/MACMan`, and the state
directory are ordinary files with ordinary permissions. Anything running as your
user can read them. MACman does not defend your Mac against your Mac.

### 8. macOS itself

TCC bypasses, resident malware, and vulnerabilities in Apple's own frameworks
are outside anything this project can address. If your Mac is already
compromised, MACman is not the problem — but it is a convenient tool for someone
who owns it.

### 9. A review by anyone but its author

**One developer, one Mac, one user.** No external audit, no penetration test,
no bug bounty. Every number in [RELIABILITY.md](RELIABILITY.md) was produced by
the person who wrote the code being measured, which is the weakest form of
evidence that exists short of none.

The case-insensitivity bug is the honest illustration: it survived a security
review, a written claim that credential paths were protected, and an
automated audit that reported success — for as long as nobody attacked it
properly.

---

## Verify it yourself

Do not take the claims above on trust; they are all reproducible.

```bash
.venv/bin/python tests/audit/injection.py   # attack the defences — any PASS is a break
.venv/bin/python tests/audit/egress.py      # can data leave without you agreeing?
.venv/bin/python tests/audit/network.py     # count outbound connections
.venv/bin/python -m macman.main preflight   # what permissions are actually granted
```

Read the audit log to see exactly what ran while you were away:

```bash
tail -f ~/Library/Application\ Support/MACman/audit.jsonl
```

Confirm the dependency list matches what is declared:

```bash
pip install --require-hashes -r requirements.lock
```

---

## Turn it all off

One command kills the process, removes the LaunchAgent so nothing restarts at
login, and deletes the TOTP secret from the Keychain — every issued code dies
with it:

```bash
.venv/bin/python scripts/revoke_all.py --revoke
```

Run it without `--revoke` first to see what exists. Add `--purge-audit` to
delete the log as well.

macOS permissions are revoked separately, and independently of this project, in
**System Settings → Privacy & Security**. Removing Full Disk Access there stops
the text channel working no matter what MACman's code does.

---

## Reporting a vulnerability

Open an issue at
[github.com/Nikhilnmv/MACman/issues](https://github.com/Nikhilnmv/MACman/issues)
— or, for anything that would expose data if published, contact the maintainer
privately first.

**Measurements that contradict this document are the most valuable
contribution anyone can make.** The case-insensitivity leak was found by
distrusting a test that said everything was fine. Please distrust the rest of it.

---

## Choices made deliberately

| Decision | Why |
|---|---|
| No password, ever | An assistant that can unlock a Mac is a remote-access trojan with good manners |
| Typed tools, no shell locally | A malformed or injected command becomes impossible to express, not merely unlikely |
| Router makes no network call | Asking the cloud whether something is private leaks it |
| Allowlist before the model | Unknown senders should not be able to make MACman think |
| Lockout state in memory | Restart clears it — an attacker who can restart the process already has code execution |
| Results logged as hashes | An audit log should not become a second copy of your data |
| Accessibility clicking not shipped | Measured 50%; a wrong click presses something real |
