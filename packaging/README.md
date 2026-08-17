# Publishing the Homebrew tap

Turning `brew install nikhilnmv/tap/macman` into a real command.

**This cannot work while the repo is private.** A Homebrew formula fetches a
public tarball; a private repo returns 404 to anyone without credentials. So
step 0 is making MACman public — everything below assumes that has happened.

---

## Why a tap and not homebrew-core

`homebrew-core` requires a stable, notable project with no unusual
requirements. MACman needs **full Xcode at build time** and asks for six TCC
permissions at runtime, which is exactly the kind of thing core reviewers
reject. A personal tap is the right home, and it is what most tools like this
use.

---

## One-time setup

### 1. Make the repo public and tag a release

```bash
cd ~/Documents/MACMan
git tag v0.1.0
git push origin v0.1.0
```

Then create a release from that tag on GitHub. Homebrew will download
`archive/refs/tags/v0.1.0.tar.gz`, which GitHub generates automatically.

### 2. Get the tarball's checksum

```bash
curl -sL https://github.com/Nikhilnmv/MACman/archive/refs/tags/v0.1.0.tar.gz \
  | shasum -a 256
```

Paste the result over `REPLACE_WITH_RELEASE_SHA256` in `macman.rb`.

### 3. Create the tap repo

It **must** be named `homebrew-tap` — Homebrew derives `nikhilnmv/tap` from
that name, and any other name breaks the short form.

```bash
mkdir -p ~/homebrew-tap/Formula
cp ~/Documents/MACMan/packaging/macman.rb ~/homebrew-tap/Formula/
cd ~/homebrew-tap
git init && git add -A && git commit -m "Add macman formula"
git branch -M main
git remote add origin git@github.com:Nikhilnmv/homebrew-tap.git
git push -u origin main
```

Create `Nikhilnmv/homebrew-tap` on GitHub first, **public**.

### 4. Fill in the Python dependencies

Homebrew vendors every Python package rather than trusting PyPI at install
time. Generate the stanzas:

```bash
brew tap nikhilnmv/tap
brew update-python-resources $(brew --repository nikhilnmv/tap)/Formula/macman.rb
```

That replaces the `# RESOURCES_GO_HERE` marker with pinned `resource` blocks —
**17 packages**, since `anthropic` is an optional extra rather than a base
dependency.

### 5. Test before announcing it

```bash
brew install --build-from-source nikhilnmv/tap/macman
brew test macman
brew audit --strict --online nikhilnmv/tap/macman
```

`brew audit` catches most things reviewers would. Fix what it reports before
telling anyone the command works.

---

## Releasing an update

```bash
cd ~/Documents/MACMan
git tag v0.2.0 && git push origin v0.2.0
curl -sL https://github.com/Nikhilnmv/MACman/archive/refs/tags/v0.2.0.tar.gz | shasum -a 256
```

Update `url` and `sha256` in the formula, re-run `brew update-python-resources`
if dependencies changed, commit to the tap repo. Users get it with
`brew upgrade`.

---

## What this does and does not give a user

**Does:** one command instead of four, Python and dependencies handled, a
public reviewable formula, and `brew upgrade` for updates.

**Does not:** make the code signed. Homebrew installs unsigned binaries built
on the user's own machine, so macOS cannot vouch that nothing was tampered
with. For a tool asking for Full Disk Access that is a genuine gap, and it is
the argument for a notarized `.app` later — worth the $99/yr **once real people
are using this**, not before.

Being straight about it: Homebrew's trust model here is "the formula is public
and the source is public, so you or anyone else can read what you are about to
run." That is meaningfully better than a random binary download, and
meaningfully weaker than a notarized app. Both statements belong in the README.
