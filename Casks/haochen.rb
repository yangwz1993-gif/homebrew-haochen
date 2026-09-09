cask "haochen" do
  version "0.3.2"
  sha256 "f8aea229f7a70fb6e8cdc8b87cd6d4e4975fe2b30cd1af1d6722f97e492a141d"

  url "https://github.com/yangwz1993-gif/homebrew-haochen/releases/download/v#{version}/haochen-#{version}.dmg"
  name "haochen"
  desc "桌面 AI 伙伴（读屏/看图/聊天的桌宠）"
  homepage "https://github.com/yangwz1993-gif/homebrew-haochen"

  depends_on :macos

  # 发布模式说明（所有者决定，2026-09-09）：App 由发布者自签身份签名，未经 Apple 公证。
  # postflight 移除 quarantine 使 Homebrew 安装路径可用（延续已发布版本的机制）。
  # 注意：仍需用户在系统设置授权「辅助功能 + 屏幕录制」（系统机制，无法通过 cask 跳过）。
  app "haochen.app"

  postflight_steps do
    run "/usr/bin/xattr",
        args: ["-dr", "com.apple.quarantine", "{{staged_path}}/haochen.app"]
  end

  zap trash: "~/Library/Application Support/haochen"
end
