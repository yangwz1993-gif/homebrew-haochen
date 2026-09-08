cask "haochen" do
  version "0.2.0"
  sha256 "6fb99d0997386417f687b95c39ef4d94d30ea570f859a7bdb8e90dd50e5c1075"

  url "https://github.com/yangwz1993-gif/homebrew-haochen/releases/download/v#{version}/haochen-#{version}.dmg"
  name "haochen"
  desc "桌面 AI 伙伴（读屏/看图/聊天的桌宠）"
  homepage "https://github.com/yangwz1993-gif/homebrew-haochen"

  # 发布模式说明（所有者决定，2026-09）：App 由发布者自签身份签名，未经 Apple 公证。
  # postflight 移除 quarantine 使 Gatekeeper 不拦截（与 0.1.x 已发布 cask 同一机制）。
  # 注意：仍需用户在系统设置授权「辅助功能 + 屏幕录制」（系统机制，无法通过 cask 跳过）。
  app "haochen.app"

  postflight do
    system_command "xattr",
                   args: ["-dr", "com.apple.quarantine", "#{staged_path}/haochen.app"],
                   sudo: false
  end

  zap trash: [
    "~/Library/Application Support/haochen"
  ]
end
