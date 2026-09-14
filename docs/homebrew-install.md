# haochen · Homebrew 分发与安装

内部里程碑版 **0.6.2-beta.1**。本地自签、未经 Apple 公证；cask 已内置 `postflight`
自动移除 quarantine，所以同事**一条命令即可安装并直接打开**，无需右键、无需 `xattr`。

---

## A. 发布者（你）：传包 + 发布 cask

tap 仓库就是本工作区（`github.com/yangwz1993-gif/homebrew-haochen`）。cask 已更新好，
只差两步：

### 1) 上传 DMG 到 GitHub Release（tag 必须是 `v0.6.2-beta.1`）

```bash
cd "/Users/yangwenzhu1/Desktop/111Workspace/260913_haochen继续开发"
gh release create v0.6.2-beta.1 \
  packaging/dist/haochen-0.6.2-beta.1.dmg \
  -t "haochen 0.6.2-beta.1" \
  -n "内部里程碑版：内网模型应用内接入 + Hi 精准跳转"
```

> 必须上传的就是 `packaging/dist/haochen-0.6.2-beta.1.dmg` 这个文件——cask 里的
> `sha256` 是按它算的（`829df344…16c`）。换文件就要同步改 sha256。

### 2) 提交并推送更新后的 cask

```bash
git add Casks/haochen.rb
git commit -m "cask(haochen): 0.6.2-beta.1"
git push
```

发布后可自检：`brew install --cask yangwz1993-gif/haochen/haochen`（能拉到即成功）。

---

## B. 使用者（同事）：一键安装

```bash
brew install --cask yangwz1993-gif/haochen/haochen
```

装完在启动台/应用程序里打开 **haochen** 即可，不会有"无法验证/已损坏"的拦截。

已装过旧版要更新：

```bash
brew upgrade --cask yangwz1993-gif/haochen/haochen || \
brew reinstall --cask yangwz1993-gif/haochen/haochen
```

卸载：

```bash
brew uninstall --cask yangwz1993-gif/haochen/haochen   # 加 --zap 连数据一起清
```

---

## C. 首次使用按需配置（不配也能用外网 DeepSeek 聊天）

- **内网模型（Kimi / GLM / Gemini 等）**：先登录本机 codewiz-cc，再在向导「连接模型」
  里选「CodeWiz 内网模型」→ 填自己的**公司邮箱 + API Key** → 点连接。
- **Hi 状态监控 / 精准跳转 / 让 haochen 查 Hi**：本机需已安装内部 `hi` CLI。
- **读屏 / 看图**：首次在「系统设置 → 隐私与安全性」授权「辅助功能」「屏幕录制」。

### Otty「Agent 状态」前置链（缺了会全部显示「状态未知」）

haochen 的 Otty 面板本身不含 Otty，也不能替你装钩子。要让 Claude / Codex / Pi 等
Agent 的状态真实上报，需要四步：

1. **安装 Otty ≥ 1.4.1**（旧版没有状态上报字段，haochen 会在诊断里提示升级）；
2. 让 Otty 保持运行；
3. 在 **Otty 设置 → Agents** 给你用的每种 Agent 安装官方 Hooks（Claude/Codex 只能
   在这里装，haochen 不改写你的 Agent 配置；Pi/OMP 可以在 haochen 看板里确认安装）；
4. **重启对应的 Agent 会话**（钩子随进程启动加载，不重启不上报）。

> 自查一句话：`/Applications/Otty.app/Contents/MacOS/otty-cli --json pane list` 里
> 每个 pane 的 `agent_state` 非空（processing/idle/awaiting）即正常；空则是钩子没装
> 或会话没重启。

**用 codewiz-cc（CW）驱动 Claude 的额外一步**：CW 会话读的是独立配置目录
`~/.cc-mirror/codewiz-cc/config/settings.json`，Otty 官方安装器不会写进去。装完
Claude 官方 Hooks 后，把 `~/.claude/settings.json` 里带 `_otty` 标记的 hooks 组
合并进该文件，然后重启 CW 会话。

---

## 说明与边界

- 本版本 **arm64（Apple Silicon）**；`depends_on arch: :arm64`。
- 自签未公证，仅限**内部试用**；对外/正式分发需 Developer ID 签名 + Apple 公证。
- 权限与钥匙串仍由用户各自控制，cask 只去掉了 app 的 quarantine 标记。
