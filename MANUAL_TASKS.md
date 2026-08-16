# Manual tasks

Everything MACman needs that I can't do for you — things needing your password,
a click in System Settings, or an account you own.

Check status any time:

```bash
.venv/bin/python -m macman.main preflight
```

**Legend:** ✅ done · 🔴 blocking · 🟡 needed for a specific phase · ⚪ later

---

## ✅ Done

These were blockers and are now cleared. Kept for reference.

| | Task |
|---|---|
| ✅ | **Swift toolchain** — reinstalled Command Line Tools; `swiftc` works |
| ✅ | **Xcode installed** + licence accepted → unlocked the `FoundationModelsMacros` plugin, which is what lets Apple's model use tools |
| ✅ | **Accessibility, Full Disk Access, Screen Recording** granted to Terminal |
| ✅ | **Automation** prompts approved for Finder, Messages and others |
| ✅ | **TOTP credential** provisioned and verified against your authenticator |
| ✅ | **Your handle** added to the allowlist |
| ✅ | **Live iMessage test** — wake phrase → code → task → end session |
| ✅ | ~~Install Ollama~~ — **no longer needed.** Apple's built-in model replaced it. You can reclaim the space: `rm -rf ~/.ollama && brew uninstall ollama` |

---

## Nothing is currently blocking

MACman works today: text it from another Apple device and it answers, using
Apple's on-device model, free and offline.

Everything below unlocks *additional* capability.

---

## 🟡 When you want the paid tier

### An Anthropic API key

Needed for the things the on-device model genuinely cannot do — understanding
images, operating apps with no scripting interface, code reasoning,
multi-application orchestration. See [CAPABILITY.md](CAPABILITY.md) §7 for the
exact boundary.

`ANTHROPIC_API_KEY` in your shell currently holds another tool's token
(starting `aero_liv`), so put MACman's key in a project-local `.env` instead of
overwriting it:

```bash
echo 'ANTHROPIC_API_KEY=sk-ant-api03-...' >> /Users/nikhilsmac/Documents/MACMan/.env
chmod 600 /Users/nikhilsmac/Documents/MACMan/.env
```

**The cloud engine has never run.** It is built and unit-tested, but no valid
key has ever been configured, so its real cost and latency are unmeasured.

### A second Apple device

iMessage and FaceTime route to *devices*, not to a specific Mac, so you need
something other than this Mac to send from. You've been testing with your
sister's iPhone, which is fine for that — but an iPad or similar of your own is
what makes daily use practical, and it's required before v3's FaceTime calling.

---

## ⚪ Before v3 (voice and FaceTime)

| | Task |
|---|---|
| ⚪ | `brew install blackhole-2ch`, then restart |
| ⚪ | FaceTime → Video → Microphone → **BlackHole 2ch** |
| ⚪ | Grant Microphone + Speech Recognition permissions |
| ⚪ | Enable "Wake for network access"; keep the lid **open** |

Only the 2ch version is needed — the incoming audio uses a Core Audio process
tap, so your speakers keep working normally.

---

## ⚪ Before v4 (shipping to others)

| | Task |
|---|---|
| ⚪ | Self-signed code-signing identity — Keychain Access → Certificate Assistant → Create a Certificate, name it `MACman Dev`, type **Code Signing** |
| ⚪ | Re-grant the five permissions once to `MACman.app` |
| ⚪ | Confirm automating Claude.app / Codex is within their terms of service |
| ⚪ | Apple Developer account ($99/yr) — **only** for notarized distribution |

TCC grants are keyed to code signature, so a stable identity stops every
rebuild from re-triggering permission prompts.

---

## Summary

| Task | Status | Unlocks |
|---|---|---|
| Toolchain, Xcode, permissions, TOTP, allowlist | ✅ | MACman works today |
| Anthropic API key | 🟡 | Vision, unscriptable apps, coding |
| Second Apple device | 🟡 | Practical daily use; required for v3 |
| BlackHole + audio permissions | ⚪ | v3 voice |
| Signing identity | ⚪ | v4 distribution |
