# Testing MACman as a stranger would

Everything measured about MACman was measured on the machine that built it, by
the person who built it. That is the weakest form of evidence there is, and no
amount of further testing *on that account* fixes it.

A second macOS user account gives what a second Mac would: an empty TCC
database, an empty home folder, no Keychain entries, no config — without risking
your working setup, and repeatable by deleting the account and making another.

**What this tests:** the install a stranger gets, the permission prompts, and
whether the setup wizard makes sense to someone who has not seen it.

**What it cannot test:** see [The iMessage
problem](#the-imessage-problem) — the text channel needs an Apple ID, and that
is the one part a second account makes awkward rather than easier.

---

## Before you start

Copy the built app somewhere both accounts can reach:

```bash
mkdir -p /Users/Shared/MACmanTest
cp -R app/build/MACman.app /Users/Shared/MACmanTest/
cp scripts/uninstall.sh scripts/permissions.py /Users/Shared/MACmanTest/
```

**Copy the app rather than the repository, deliberately.** The test account
should have no Xcode, no virtualenv and no source — because that is what a
release install looks like, and it verifies the bundle really is self-contained
rather than quietly reaching back into a checkout.

Verified before writing this: the bundle runs from `/tmp` with an empty
environment, finds its own Python and its own helpers, and reports the
on-device engine available with tools enabled.

---

## 1. Create the account

**System Settings → Users & Groups → Add Account…**

- Name it something obvious like `MACman Test`
- **Standard**, not Administrator — a stranger installing this would not
  necessarily be an admin, and if MACman needs admin rights, that is worth
  discovering here rather than from a bug report

Log out, log in as the test user.

## 2. Confirm the account is genuinely clean

```bash
/Users/Shared/MACmanTest/permissions.py
```

Expect **every permission not granted**, and the responsible app named as your
terminal. If anything is already granted, the account is not clean and the test
is worth less.

Also check nothing survives from the other account:

```bash
ls ~/Library/Application\ Support/MACman 2>&1     # should not exist
security find-generic-password -s com.macman.totp 2>&1   # should fail
```

## 3. Install and run

```bash
open /Users/Shared/MACmanTest/MACman.app
```

**Stop here and notice what happens**, because this is the moment a stranger
decides whether to trust it:

- Does Gatekeeper complain? The app is ad-hoc signed, so it may. **Write down
  exactly what the dialog says** — that wording is the first impression, and it
  is the strongest argument for or against paying for signing.
- Does the menu bar icon appear?
- Does it say **Not listening**, with a reason?

## 4. Walk the wizard as if you had never seen it

Menu bar → **Set up MACman…**

Go through all six steps *without* using anything you know from having built
it. The questions worth answering are not "does it work" but:

| At each step | Ask yourself |
|---|---|
| Welcome | Would this make me more or less willing to continue? |
| Permissions | Do I understand what I am granting, and why? |
| | Does the status dot actually flip when I grant it? |
| Who can reach me | Is the handle format obvious? What happens if I typo it? |
| Your code | Is it clear the QR *is* the secret? |
| Engine | Is it clear that on-device is the default and costs nothing? |
| Check it works | Does the result convince me nothing left the Mac? |

**Skip a permission deliberately** and check that MACman explains what it
disabled rather than refusing to continue.

## 5. Check what it actually created

```bash
ls -la ~/Library/Application\ Support/MACman/
security find-generic-password -s com.macman.totp >/dev/null && echo "login code stored"
/Users/Shared/MACmanTest/permissions.py
```

The permissions check should now name **MACman.app** as the responsible app —
not your terminal. That is the whole permission model, observed on a clean
machine.

## 6. Remove it, and verify

```bash
/Users/Shared/MACmanTest/uninstall.sh            # dry run
/Users/Shared/MACmanTest/uninstall.sh --yes
```

Then check nothing is left:

```bash
ls ~/Library/Application\ Support/MACman 2>&1     # should not exist
ls ~/Pictures/MACMan 2>&1                          # should not exist
security find-generic-password -s com.macman.totp 2>&1    # should fail
security find-generic-password -s com.macman.cloud 2>&1   # should fail
```

Finally reset the permissions the account granted:

```bash
tccutil reset All com.nikhilnmv.macman
```

## 7. Delete the account

**System Settings → Users & Groups →** remove `MACman Test`, choosing to delete
the home folder. Everything the test created goes with it.

---

## The iMessage problem

The text channel is the product, and it is the part a second account makes
harder rather than easier.

Messages needs an Apple ID signed in. Your options, none of them clean:

| Option | Consequence |
|---|---|
| Sign the **same Apple ID** into the test account | Works, but your main account also receives every test message. Messages sync across both |
| Use a **second Apple ID** | Cleanest, but that Apple ID must then be the one you text *from*, so you need a third device or another ID |
| **Skip the text channel** in this test | Everything else — install, permissions, wizard, self-test, removal — is tested faithfully |

**My recommendation: skip it here.** The iMessage round-trip is already proven
(8 authenticated sessions in August), and it is the *setup and permission*
experience on a clean machine that has never been tested. Test the text channel
on your main account, where it already works, using
[VERIFY.md](VERIFY.md).

If you do sign in an Apple ID, add the *sending* device's handle to the
allowlist in the test account's settings — not the one from your main account.

---

## What to write down

The point of this exercise is friction, not confirmation. For each step:

1. **Anything you had to guess.** A stranger cannot ask you.
2. **Anything that looked broken but was not** — a status that reads wrong is a
   bug even when the code is right.
3. **The exact Gatekeeper wording**, if it appears. That single dialog decides
   whether the $99 signing question is urgent or not.
4. **How long it took**, honestly. [TESTING.md](TESTING.md) claims about 30
   minutes.

The most valuable finding is a moment where you thought *"I only know to do
this because I built it."* Every one of those is a step a stranger fails at.
