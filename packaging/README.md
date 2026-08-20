# Publishing MACman

Everything here is a *copy* of what Homebrew needs, kept in the main repo so it
stays in step with the code.

## Why a cask, not a formula

MACman is an app bundle that must hold its own TCC permissions. macOS
attributes a permission to the app that launched a process, so the daemon has
to be a child of `MACman.app`. A formula installing a CLI would put the grants
back on Terminal, which covers every script the user ever runs in a shell —
the exact problem the app exists to solve.

The cost is quarantine. `brew install --cask` sets `com.apple.quarantine`;
formula installs do not. So an unsigned cask install shows "unidentified
developer". The old formula avoided that by giving up the property that matters
more.

## Publishing a release

```bash
./scripts/release.sh
```

Builds a release bundle with the runtime embedded, packages it with `ditto`
(which preserves the code signature — plain `zip` can silently break it),
verifies the signature survives the round trip, checks the unpacked app reports
the right version, and prints the `version` and `sha256` to paste into the
cask.

It stops there. Tagging, pushing and publishing are public and hard to undo, so
it prints those commands rather than running them.

## The tap

Homebrew requires a repository named `homebrew-tap` with a `Casks/` directory.

1. Create `Nikhilnmv/homebrew-tap` on GitHub
2. Copy `packaging/Casks/macman.rb` into `Casks/macman.rb` there
3. Update `version` and `sha256` from `release.sh`
4. Commit and push

Users then:

```bash
brew tap nikhilnmv/tap
brew install --cask macman
```

## The signing decision

Unresolved, and the install experience depends on it:

| Path | Cost | What the user sees |
|---|---|---|
| Sign and notarize | $99/yr | Installs and opens cleanly |
| Ship unsigned | £0 | "Unidentified developer"; needs `--no-quarantine` or right-click → Open |
| Stay a formula | £0 | No warning, but needs Xcode and a build |

For a tool that then asks for Full Disk Access, the middle option is worse than
it sounds — the first thing it teaches is how to bypass Gatekeeper. Worth
deciding after watching a real first-run in a clean account
([FRESH_INSTALL.md](../docs/FRESH_INSTALL.md)), which is what that test is for.

## Version

`macman/__init__.py` is the single source. `app/build.sh` stamps it into
`Info.plist`, and `release.sh` reads it for the archive name and the cask.
Nothing else should declare a version.
