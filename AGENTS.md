# haochen

## 一句话说明
macOS 桌面 AI 伙伴（PyQt6 + WebKit 壳 + Bun 编译的 pi 引擎）：桌宠聊天 + 总览看板（Otty/Chrome/日历/微信/Hi 多源聚合），内部 legacy 自签 DMG 分发（未 Apple 公证）。

## 业务背景与术语
内部工具，分发给同事经 Homebrew tap（`yangwz1993-gif/haochen`）。本轮主题 = 用户视角问题治理（看板 feed/刘海/连接态/Hi）。
术语表：
- 刘海：notch 动效展示区（对标 MioIsland）；桌面总览：dashboard 看板；Otty：本机 agent 状态探测模块
- 连接态/内容态：C-11 二维状态分离（service 派生 connection/coverage，前端三处统一，commit 59cd4a6）
- v4 准出：新行为测试+不变量测试+红绿变异存证（证据归档 evidence/）
- 占位降权：占位卡=背景式虚线弱样式，永不假装未读消息
- 治理编号：A=已交付 7 项 / B=冒烟新发现 23 项 / C=长期结构改进

## 快速上手（务必照做，否则门禁/构建失败）
```bash
export NVM_DIR="$HOME/.nvm"; . "$NVM_DIR/nvm.sh"; nvm use 22.19.0   # Node 必须 22.19.0（.node-version）
export PATH="$HOME/.local/bin:$PATH"
UV_DEFAULT_INDEX="https://mirrors.aliyun.com/pypi/simple/" UV_PROJECT_ENVIRONMENT="$PWD/app/.venv" uv sync --frozen  # 直连会卡死
make check                    # 全量门禁：800+ pytest + ruff + pyright + shellcheck + node + bun + 测试完整性 ratchet
HAOCHEN_BUILD_MODE=legacy bash packaging/build.sh   # 出包 → packaging/dist/haochen-<VERSION>.dmg
# 升版：改 VERSION → app/.venv/bin/python scripts/version.py sync → 手改 browser-extension/manifest.json → render-cask
# 装机：退出旧 App → cp -R packaging/dist/haochen.app /Applications/（签名身份 haochen Local Signing 固定，保 TCC/钥匙串）
```

## 架构与模块地图
- `app/haochen_app/`：壳（app_shell 入口/onboarding/engine_client/codewiz/keychain）
- `app/haochen_app/dashboard/`：看板服务（controller/service/store + adapters/{hi,otty,browser,calendar,wechat}.py + attention.py 提醒语义 + notch.py 刘海）
- `app/assets/dashboard/`：前端三件套（index.html/dashboard.js/dashboard.css/icons.js）
- `app/ext/index.ts`：agent 扩展（hi_lookup/read_screen 等工具）
- `pi-source/`：引擎源码（Bun 编译，`engine/build.sh`）
- `packaging/`+`scripts/version.py`+`cask/`：打包/升版/cask 模板（cask 改 template，不直接改生成物）
- `tests/`：pytest 全套 + `tests/baseline.json`（ratchet 基线）+ `scripts/check_test_integrity.py`

## 约定与边界（铁律）
- 测试 ratchet：用例数/测试文件/skip 标记/覆盖率锚定基线**只增不减**（防作弊修复/破坏式修复）
- 每个修复必须带红-绿-变异三段存证（v4 准出），证据归档 `evidence/`
- 占位卡永不假装未读；提醒只在「未读且待处理」时触发且指名应用；不抢键盘焦点
- 推送/发布/公证需用户明确授权；legacy 仅内部 Homebrew 通道
- 汇总产物带时间戳/版本后缀，绝不覆盖已有文件；仓库内文档就地更新靠 git

## CHANGELOG
见根目录 CHANGELOG.md 与 docs/handoff/（append-only）

## 当前状态
先读 docs/STATUS.md（从这里开始）
