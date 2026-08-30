cask "haochen" do
  version "0.1.10"
  sha256 "05b80643e1012416fd18778d64a99cc5cc2d1f58929391f0fc9cceee9be52501"

  url "https://github.com/yangwz1993-gif/homebrew-haochen/releases/download/v#{version}/haochen-#{version}.dmg"
  name "haochen"
  desc "桌面 AI 伙伴（读屏/看图/聊天的桌宠）"
  homepage "https://github.com/yangwz1993-gif/homebrew-haochen"

  # 自签证书，非 Apple Developer 签名；cask 安装后自动去隔离标记，避免 Gatekeeper 拦截。
  # 注意：仍需用户在系统设置授权「辅助功能 + 屏幕录制」（系统机制，无法通过 cask 跳过）。
  app "haochen.app"

  postflight do
    # 去掉隔离标记，避免 Gatekeeper 拦截（对齐 Homebrew --no-quarantine 行为）
    system_command "xattr",
                   args: ["-dr", "com.apple.quarantine", "#{staged_path}/haochen.app"],
                   sudo: false
  end

  zap trash: [
    "~/Library/Application Support/haochen"
  ]
end
