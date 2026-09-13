cask "haochen@beta" do
  version "0.5.0-beta.3"
  sha256 "644174b860e6af5a723b313a282576f1a69687eeeb542863780e1ad17a80ec3d"

  url "https://github.com/yangwz1993-gif/homebrew-haochen/releases/download/v#{version}/haochen-#{version}.dmg"
  name "haochen Beta"
  desc "Desktop AI companion and activity dashboard (beta)"
  homepage "https://github.com/yangwz1993-gif/homebrew-haochen"

  depends_on :macos
  depends_on arch: :arm64
  conflicts_with cask: "haochen"

  app "haochen.app"

  # Owner-approved legacy beta distribution: stable local signing, no Apple notarization.
  # This only removes quarantine from this app; Keychain and privacy permissions remain user-controlled.
  postflight_steps do
    run "/usr/bin/xattr",
        args: ["-dr", "com.apple.quarantine", "{{staged_path}}/haochen.app"]
  end

  caveats <<~EOS
    This is an Apple Silicon beta, not a fully accepted stable release.
    Otty agent lifecycle reporting and WeChat unread detection remain incomplete.
    Chrome requires the bundled unpacked extension and explicit page authorization.
    This app is locally signed, not Apple notarized. See README for the full limitations.
    Stable and beta share haochen.app and its data; do not install both together.
  EOS
end
