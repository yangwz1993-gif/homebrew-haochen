cask "haochen" do
  version "0.1.10"
  sha256 "05b80643e1012416fd18778d64a99cc5cc2d1f58929391f0fc9cceee9be52501"

  url "https://github.com/yangwz1993-gif/homebrew-haochen/releases/download/v#{version}/haochen-#{version}.dmg"
  name "haochen"
  desc "桌面 AI 伙伴（读屏/看图/聊天的桌宠）"
  homepage "https://github.com/yangwz1993-gif/homebrew-haochen"

  # 正式发布必须通过 Developer ID 签名与 Apple 公证；绝不绕过 Gatekeeper。
  app "haochen.app"

  zap trash: [
    "~/Library/Application Support/haochen"
  ]
end
