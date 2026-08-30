# haochen —— 给其他 Agent 的交互指令

> 这是传给其他 agent（或协作方）的指令。对方按此安装 haochen 并与之交互。

## 一、haochen 是什么
haochen 是一款 **macOS 桌面 AI 伙伴**（桌宠）：桌面上有个像素眼镜小哥，能：
- **读屏**：读取其它窗口/浏览器内容（需「辅助功能」授权）。
- **看图**：识别屏幕上的图片/画面（需「屏幕录制」授权）。
- **聊天**：直接对话，像朋友闲聊；技术问题保持专家口吻。
- 气泡固定在人物正上方，可一起拖动，位置会记住。

## 二、怎么安装（Homebrew cask）
```bash
# 1) 添加 tap
brew tap yangwz1993-gif/homebrew-haochen
# 2) 信任该 tap（Homebrew 6 要求第三方 tap 先信任）
brew trust yangwz1993-gif/homebrew-haochen
# 3) 安装
brew install --cask haochen
```
> 装有需要外部版本时：`brew install --cask haochen`（最新）。cask 的 url/sha256 已绑定版本。
> 卸：`brew uninstall --cask haochen`

## 三、首次使用（必做）
打开 haochen 后，到 **系统设置 → 隐私与安全性** 手动授权两项（macOS 机制，无法跳过）：
- **辅助功能** → 打开 haochen（用于读屏）
- **屏幕录制** → 打开 haochen（用于看图/截屏）

## 四、怎么和 haochen 交互
haochen 是**图形 app（桌宠）**，不是 CLI/API。和它交互就是**在它窗口里用自然语言打字/说话**：
- 直接说需求，它会读屏 / 看图 / 回答。
- 让读屏时点允许（可回车确认）；不让读就点「不读」。
- 首次打开它会问你怎么称呼，记住后用它称呼你。

## 五、给 agent 集成用的要点（如需程序化调用）
- 当前 haochen **没有稳定公开的 CLI/API**（图形 app）。
- 若要脚本/自动化：可唤起 app + 通过 UI 输入；或读取其日志/授权状态（`tccutil` 判断授权、`codesign -dvv` 查签名）。
- 授权检测：`tccutil reset Accessibility com.haochen.app` 清授权记录；重拨后需重启 app 生效（屏幕录制）。

## 六、版本与签名
- 当前版本：**0.1.7**，签名 `haochen Local Signing`（自签，非 Apple Developer）。
- **自签证书** → cask 安装已自动去隔离标记（Gatekeeper 不拦），但**外发给人需对方授权一次**；如需正式分发（App Store/完全免警告）需 Developer ID + 公证（本期未做）。
