# P5 交付：打包 dmg（研发 → 产品）

> 日期：2026-08-25 ｜ 交付方：kimi（研发）｜ 依据：开发总纲 §二 P5、product-acceptance §0
> 状态：**请求产品审查**。dmg 产出 + 冻结 App 真机验证完成（含真机 UI 驱动问答实证）。

---

## 一、产出物

| 产物 | 路径 | 说明 |
|---|---|---|
| 一键构建脚本 | `packaging/build.sh` | 读屏执行体→PyInstaller→资源内嵌→LSUIElement→ad-hoc 签名→dmg，全链可重复 |
| App | `packaging/dist/haochen.app`（147 MB） | onedir；`Contents/Resources/`：engine（Bun 单文件 arm64）、haochen-reader（内嵌读屏执行体）、ext/index.ts、config/ 模板、assets/pet |
| 安装镜像 | `packaging/dist/haochen-0.1.0.dmg`（68 MB） | UDZO 压缩；含 `Applications` 拖装链接；挂载/卸载实测通过 |

## 二、产品三项重点落实

### 1) .app 辅助功能权限引导 ✅
- 新增 `app/haochen_app/permissions.py`：首启检测未授权 → 引导卡片（说明 + 「去授权」触发系统授权弹窗 + 直达设置面板 + 「我已授权」回查 + 「暂不」降级可用）；
- **真机实证**（launchd 启动、.app 即责任进程）：日志 `accessibility granted at startup: False` → 引导卡片真实弹出（AX 树取证：标题/两步文案/三按钮齐全）→ AppleScript 驱动「去授权」→ 系统设置辅助功能面板自动打开；
- 未授权时全局热键自动降级双击唤起（日志有 WARNING），引导卡片明示「不授权也能用，仅读屏不可用」。

### 2) 内嵌独立读屏执行体 ✅
- 新增 `app/reader/haochen_reader.py`（335 行，移植自旧版 reader，去旧项目依赖，图片下载改 stdlib）；PyInstaller onefile 冻结为 `haochen-reader`（10.9 MB）放 Resources；
- 壳 spawn 引擎自动注入 `HAOCHEN_PETREAD=<Resources>/haochen-reader`（engine_client.spawn_argv），**不再依赖 `~/.local/bin/haochen`**；
- 冻结二进制独立实测：`haochen-reader --json` 直读前台窗口成功（cmux 12 段文本）。

### 3) LSUIElement + 签名 + dmg 干净可用 ✅
- `LSUIElement=true` 已注入；System Events 前台进程列表无 haochen（Dock 无图标实证）；
- `codesign --verify --deep --strict` 通过（ad-hoc，--deep 一并签引擎/读屏执行体）；
- 冻结 App 真机验证（`open` 启动、干净 HAOCHEN_HOME=/tmp/haochen-l4）：
  - 首启：配置模板初始化 + **key 只读导入**（~/.pi 只读）✅；
  - 资源路径全对（日志：engine/ext/assets/reader exists=True，pet 贴图零告警）✅；
  - 桌宠上屏（96×96 layer 8）✅，双击唤起气泡 ✅；
  - **真机 UI 驱动问答**：CGEvent 双击桌宠→气泡唤起→粘贴问题→回车→真 DeepSeek 两步回答，会话 jsonl 实测 `【answer】…【/answer】`+`【summary】…【/summary】` 标记完整 ✅；
  - 引擎子进程随 App 启停 ✅。

## 三、构建链技术要点（文档缺口补齐，总纲 §二 P5 要求）

- **arch 策略**：arm64 only（引擎/读屏执行体/主包均 arm64；总纲既定）。
- **hardened runtime / entitlements**：本期 ad-hoc 不启用；公证前置时在 build.sh 加 `--options runtime` + entitlements 文件（disable-library-validation 预评估：Qt/引擎 dylib 同包 ad-hoc 签，预期不需要，公证前复验）。
- **引擎随 --deep 签名**：已验证（verify --deep --strict 过）。
- **frozen 路径解析**：新增 `app/haochen_app/paths.py`（Resources 布局唯一来源）；engine_client/config_store/pet_window 全部改走 paths（dev/frozen 双形态，dev 回归 25/25 通过）。
- **冻结日志**：`HAOCHEN_HOME/logs/app.log`（windowed 无控制台，排障必备）。

## 四、验证结果汇总

| 项 | 结果 |
|---|---|
| 构建链可重复（build.sh 两次全跑） | ✅ |
| 签名/LSUIElement/Dock 无图标/dmg 挂载 | ✅ |
| 冻结 App 首启（配置/key/引导卡片） | ✅ 真机实证 |
| 冻结 App 真机问答（UI 驱动，真模型，两步标记） | ✅ 会话 jsonl 实证 |
| 内嵌读屏执行体（独立运行） | ✅ |
| dev 形态回归（mock 集成 25/25） | ✅ 路径改造后无回归 |

## 五、已知限制 / L4 遗留（如实）

1. **跨机分发**：ad-hoc 签名在他机被 Gatekeeper 拦（"已损坏"提示），自用需 `xattr -d com.apple.quarantine`；对外分发 = Developer ID + 公证（总纲 §五 既定，决定外发时再买证书）。
2. **干净账户 L4**：本机新用户账户双击安装为人工项（无法自动化建账户）；本次以「干净 HAOCHEN_HOME + launchd 启动 + UI 驱动」最大近似覆盖。建议产品/用户做一轮真机终审（当前 App 就在跑着：/tmp/haochen-l4 数据目录）。
3. **权限引导截图**：研发环境无「屏幕录制」权限（截图只得桌面壁纸），引导卡片证据以 AX 树文本+窗口几何+日志代替；若需真截图请用户手动 Shift-⌘-4 或给 cmux 授屏幕录制权。
4. 首次真链路读屏曾遇一次 TCC 瞬态失败（P4 报告 §五.1），本机二次验证及冻结形态均成功；生产引导流程已兜底。
5. dmg 未做美化（背景图/图标排版）；App 图标为 PyInstaller 默认（像素小哥图标待美术资源，P6 可补）。

## 六、下一步（P6 回归打磨）

全量回归（L1+L2+L3+L4）、长时运行稳定性、多模型切换、精细动效、QA agent 人肉模拟分屏验证。等 P5 过审开工。

—— kimi（研发）
