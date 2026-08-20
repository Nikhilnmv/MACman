# Homebrew cask for MACman.
#
# Lives in the main repo so it stays in step with the code, and is *copied*
# into Nikhilnmv/homebrew-tap when a release is published — Homebrew requires
# a `Casks/` directory in a repository named `homebrew-<tap>`.
#
# ## Why a cask and not a formula
#
# MACman is an application bundle that must hold its own TCC permissions.
# macOS attributes a permission to the app that launched a process, so the
# daemon has to be a child of MACman.app — a formula installing a CLI would
# put the grants back on Terminal, which is the thing this design exists to
# avoid.
#
# The trade is quarantine. `brew install --cask` sets com.apple.quarantine on
# what it downloads, so an unsigned build shows "unidentified developer".
# Formulae are not quarantined, which is why the old formula avoided the
# problem — by giving up the property that matters more.
#
# ## Updating this
#
#   ./scripts/release.sh
#
# prints the version and sha256 to paste below, and refuses to let a broken
# archive through by verifying the signature survives packaging.

cask "macman" do
  version "0.1.0"
  sha256 "REPLACE_FROM_scripts/release.sh"

  url "https://github.com/Nikhilnmv/MACman/releases/download/v#{version}/MACman-#{version}.zip"
  name "MACman"
  desc "Text your Mac and it does the thing, using an on-device model"
  homepage "https://github.com/Nikhilnmv/MACman"

  # macOS 15 is the floor the app declares; the on-device engine needs 26 with
  # Apple Intelligence, which MACman reports at runtime rather than blocking
  # the install over — the CLI and voice paths are useful without it.
  depends_on macos: ">= :sequoia"

  app "MACman.app"

  # MACman keeps nothing off this machine: no account, no server, nothing to
  # cancel. Everything it creates is listed here so `brew uninstall --zap`
  # leaves the Mac as it was found.
  #
  # `~/Pictures/MACMan` is easy to miss and matters most: it holds screenshots
  # MACman attached to replies.
  zap trash: [
        "~/Library/Application Support/MACman",
        "~/Pictures/MACMan",
        "~/Library/LaunchAgents/com.macman.agent.plist",
      ],
      delete: [
        "~/Library/Caches/com.nikhilnmv.macman",
      ]

  caveats <<~CAVEATS
    MACman needs permissions before it can do anything. Open it, then use
    "Set up MACman…" in the menu bar — the wizard explains what each
    permission is for and what refusing it disables.

    Grant Full Disk Access to MACman.app, not to Terminal. Granting it to
    Terminal hands the same access to every script you ever run in a shell.

    Two things `brew uninstall --zap` cannot do:

      * Your Keychain entries. Remove them with:
          security delete-generic-password -s com.macman.totp
          security delete-generic-password -s com.macman.cloud

      * macOS permissions. Revoke them in System Settings → Privacy &
        Security, or:
          tccutil reset All com.nikhilnmv.macman

    A program able to switch off its own oversight would be the wrong design,
    so MACman does not try.
  CAVEATS
end
