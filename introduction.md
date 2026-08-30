# haochen · 项目主页 / 介绍文档

> 本文件是新项目空间 `haochen new/` 的总介绍。这里整理了「上一版本代码」、pi 官方最新源码、以及 macOS App 开发规范，作为独立 macOS App「haochen」的开发起点。

---

## 一、这个目录是什么

```
haochen new/                        ← 独立 macOS App「haochen」项目工作区（新建）
├── previous-version/               ← 上一版本全部代码（petread 桌宠 + haochen-app 产品 + 文档），
│                                      排除 npm 依赖/缓存，保留可继续开发的产品逻辑与历史方案
├── pi-source/                      ← pi 官方最新源码（v0.84.3，fork 起点），engine-src 全量
├── macos-app-dev-reference/        ← macOS App 开发规范参考（打包/签名/公证/权限），见下
│   └── MACOS_APP_DEV_REFERENCE.md
└── introduction.md                 ← 本文件
```

**为什么建这 + 为什么叫 haochen**：从「套壳 pi 的桌宠」进化到「独立 agent 产品」，最终目标是**独立 macOS App**。此目录是新阶段的工作区——把上一版（petread/haochen-app）、pi 源码、macOS 规范分门别类，方便从头用 pi 源码构建我们自己的应用。

---

## 二、当前完成的（上一版本已跑通的）

- **haochen 独立 agent 桌宠**（基于 petread 迁移）：
  - 像素眼镜小哥桌宠，双击/`⌃⌥P` 唤起输入气泡；读屏前确认；页面变化检测；`answer → summary` 两步；回车发送/⌘回车换行；点空白收起。
  - 技术专家人格（读屏/查证/跑命令、不装懂、结论先行）。
  - 读屏（AX）CLI、聊天对话（L1 短结 + L2 详答）。
  - 已修关键 bug：pi 0.73 误把工具 isError:true 记 false（侧车文件方案）；气泡自绘崩溃（QPolygon→QPoint 类型）；summary 重复；双击退出。
- **引擎**：验证 pi 0.84.3（`@earendil-works/pi-coding-agent`）RPC 协议兼容；`--no-extensions -e 专属扩展` 隔离加载；两步协议改 Python 侧踢（`haochen-summary-phase`）；全局 pi 插件已清、已升至官方 0.84.3。
- **成果文档**：`previous-version/` 下有里程碑分析、交接文档、体验/美化方案、阶段总结、下阶段方案等完整过程沉淀。

---

## 三、整体产品规划

**一句话**：haochen = 独立的「技术专家 agent」桌面应用，有自己的引擎、对话/配置前端、桌面宠物，与本机 pi 完全隔离。

**三段演进**：
1. **套壳期（已完成）**：桌宠 + pi 引擎（RPC），改名人设、解耦。
2. **独立引擎期（进行中）**：用 pi 源码、去外部依赖，做成自己的引擎（E1：Bun 打包 pi 核心为单文件可执行）。
3. **独立 App 期（下一阶段）**：打包成 macOS App（.dmg），含完整对话 + 配置前端 + 桌面宠物。

**产品形态**（终态）：
- 桌面宠物（像素眼镜小哥）常驻唤起。
- 完整对话 UI（流式/Markdown/工具过程/会话管理）。
- 配置 UI（模型/API Key/provider/主题/行为）。
- 读屏、跑命令、写文件等工具能力（来自 pi 逻辑，去外部依赖）。
- 与本机 pi 零共享（配置、会话、依赖）。

---

## 四、下一阶段要完成的 App 开发（重点）

见 `previous-version/haochen-app/下阶段方案.md`，核心：

- **置顶目标**：独立 macOS App「haochen」——用 pi 源码**只拷贝代码逻辑、去掉外部依赖**，交付 **.dmg**，含**完整对话前端 + 配置前端 + 桌面宠物**。
- **引擎承载**：**E1**——用 Bun 把 pi 核心（coding-agent agent 循环/工具/会话/provider）打包成单文件可执行，逻辑零改写、外部依赖 bundle 进单文件；App 前端用 Python+PyQt，.app 内嵌引擎可执行。
- **模块拆解**：M-A 引擎打包(E1, 2–5 天) / M-B 模型层(1–2) / M-C 对话前端(5–8) / M-D 配置前端(3–5) / M-E 桌宠整合(3–4) / M-F dmg 打包(4–7) / 联调测试(5–8)。**合计约 23–39 人天**。
- **里程碑①（验证地基）**：Bun 把 pi 核心打成能跑 `--mode rpc` 的单文件可执行——跑通则项目成立。

---

## 五、macOS App 开发规范（引述）

详见 `macos-app-dev-reference/MACOS_APP_DEV_REFERENCE.md`，要点：
- **.app 结构**：Contents/{MacOS 主可执行, Resources 内嵌引擎, Info.plist}。
- **签名**：`codesign --deep --sign -`（ad-hoc）或 Developer ID 证书。
- **公证**：`notarytool submit` + `stapler staple`，否则 Gatekeeper 拦。
- **沙盒权衡**：本项目读屏需辅助功能/屏幕录制权限，**非沙盒**，直接 .dmg 分发。
- **权限**：辅助功能（AX）必须、屏幕录制（截图 OCR 兜底时），需在系统设置授权。
- **打包**：PyInstaller(on-dir) + 组 .app + `hdiutil create` 打 dmg；或 py2app 直出 .app。

---

## 六、下一步 action

1. 验证 E1：Bun 打包 pi 核心 → 能跑 `--mode rpc` 的单文件可执行（里程碑①）。
2. 用 `previous-version/` 的桌宠/读屏/UI 基础，接上自研引擎。
3. 逐步做对话/配置前端 → 整合桌宠 → 打包 .dmg。
