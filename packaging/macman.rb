# Homebrew formula for MACman.
#
# Lives here in the main repo so it stays in step with the code; it is *copied*
# to the tap repo (Nikhilnmv/homebrew-tap) on release. See packaging/README.md.
#
# Two things worth knowing about this formula:
#
#   * It installs the free tier only. `anthropic` is an optional extra, so a
#     default install pulls 11 packages rather than 35, and nothing from a
#     cloud vendor is required to run MACman offline. That count is measured
#     by a clean install, not estimated — see requirements.lock.
#   * It needs full Xcode, not Command Line Tools. Apple's tool-calling macros
#     (FoundationModelsMacros) ship only with Xcode, and without them the
#     on-device model cannot use tools — which is most of the product.
class Macman < Formula
  include Language::Python::Virtualenv

  desc "Text or talk to your Mac — on-device, private, no API key"
  homepage "https://github.com/Nikhilnmv/MACman"
  url "https://github.com/Nikhilnmv/MACman/archive/refs/tags/v0.1.0.tar.gz"
  sha256 "REPLACE_WITH_RELEASE_SHA256"
  license "MIT"

  depends_on "python@3.13"
  depends_on xcode: ["26.0", :build]
  depends_on :macos
  # Apple's on-device model ships with macOS 26; earlier versions have no
  # free engine at all, so the formula refuses rather than half-installing.
  depends_on macos: :tahoe

  # Generated with `brew update-python-resources Formula/macman.rb`.
  # Placeholder until the first tagged release exists — see packaging/README.md.
  # RESOURCES_GO_HERE

  def install
    virtualenv_install_with_resources

    # Build the Swift helpers: on-device model, speech, audio tap,
    # accessibility and lock state.
    system "swift", "build",
           "--package-path", "helpers",
           "-c", "release",
           "-Xswiftc", "-DMACMAN_TOOLS"

    helpers = %w[macman-local macman-speech macman-audio macman-ax macman-state]
    helpers.each do |name|
      libexec.install "helpers/.build/release/#{name}"
    end
  end

  def caveats
    <<~EOS
      MACman needs no permissions to start. Each one you grant adds a feature,
      and it will tell you what each unlocks:

        macman setup

      Nothing is requested up front. The everyday half — files, system control,
      apps, developer tools — runs entirely on your Mac using Apple's built-in
      model. No API key, no network, no cost.

      For the Claude tier (vision, unscriptable apps, code reasoning):
        pip install 'macman[cloud]'   and add a key to .env

      To turn everything off at any time:
        macman-revoke --revoke
    EOS
  end

  test do
    # Doesn't require permissions or a model — just proves the CLI is wired up.
    assert_match "macman", shell_output("#{bin}/macman --help")
    assert_match "Working now", shell_output("#{bin}/macman preflight", 0)
  end
end
