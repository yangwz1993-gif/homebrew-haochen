# haochen Chrome 网页追踪 · 0.5

真实数据通路：用户点击「授权此网站并追踪当前页」→ Chrome 逐站权限 →
主框架隔离世界读取文字 → Native Messaging → 本机 haochen 私有数据库。
不会访问外网 API、读取其他未选择页面、Cookie、密码或表单内容，不在无痕模式工作。

## 本地验收安装

1. 在 Chrome 打开 `chrome://extensions`，启用开发者模式。
2. 点击「加载已解压的扩展程序」，选择此 `browser-extension` 文件夹。
3. 本版配套扩展使用固定公开标识 `hjbiopmhlpcnpaojialipdhdemnafecb`，解压位置变化不会改变 ID。
4. 在 haochen 的 Chrome 来源连接页点击「完成本机连接」。这一步仅注册本机桥接，不会读取页面或自动授权网站；自行构建的扩展或旧版 ID 不同时，可以在扩展「连接帮助」复制实际 ID 后填写。
5. 返回扩展点击「重新连接」。打开要追踪的网页，点击「授权此网站并追踪当前页」，确认 Chrome 权限请求。
6. haochen 的浏览器动态应出现真实来源。若桥接离线，请检查是当前版本的 app，并在移动/替换安装位置后重新安装桥接。

普通用户分发需将本扩展上架 Chrome Web Store 或采用受管理部署，固定商店 extension ID；
当前本地验收使用解压扩展流程，不冒充已经完成商店发布。固定 ID 来自 manifest 中的公开 key，不是商店签名，也不授予任何站点权限。原生应用本身不需要 Node 或 Python 外部环境。

## 追踪行为与真实边界

- 每个来源有稳定 UUID 与明确 URL；同页重绑保持 UUID。导航到其他 URL 后显示 `target_changed`，不会读错页。
- 变化合并等待约 1.2 秒；每 30 秒做一次检查。浏览器休眠、进程暂停和计时器限流可能延迟，超过 95 秒无实际新检查会明确标旧。
- 页面关闭、权限撤回、标签页休眠、采集失败、桥接离线分别显示状态；不会把旧内容当成新数据。
- 仅主框架 DOM 文字，最多 60,000 字符；超限标记截断。图片、Canvas、跨域 iframe、Shadow DOM、未加载内容不在覆盖范围内。
- 「打开来源」通过原浏览器配置的本机桥接，复验来源、标签、窗口、网址及站点授权，再定位原标签。只有 Chrome 确认聚焦成功才返回成功；标签关闭或网址改变时明确失败，不擅自新开网页。
- 正文只在 app home 的 `dashboard/browser-bridge/observations.sqlite3` 保存一份最新快照，目录 0700、文件 0600；最多 64 个来源，7 天不再观测的记录不再供读取并在下次桥接连接清理。扩展自己的本机 storage 只保存元数据和追踪选择，不保存正文。
- 来源文字是未受信任的材料，不能作为系统指令、不能指挥执行 shell/消息发送。

## 移除

- 弹窗「移除追踪」停止对应观察器并删除本机来源快照；桥接离线时删除请求等待重新连接后执行。
- 需要彻底撤回站点访问，可在 Chrome 扩展设置中移除该站点权限。
- haochen 的「移除浏览器桥接」仅删除自己安装的 native host manifest 与 launcher；不会删历史其他应用数据。再在 `chrome://extensions` 移除扩展即可。

## 开发接口

`BrowserAdapter(home).snapshot()` 返回连通性与真实来源列表；`evidence(sourceId)` 仅新鲜 available 状态返回正文。
对外时间字段均为 ISO8601 UTC。`evidence` 也接受精确已追踪 URL，不会发起任意 URL 抓取。
`install_host(extension_id)` 是必须由用户显式点击触发的动作，不能在应用启动时调用。
主 app 在 Qt 初始化前分派 `--browser-host` 到 `run_browser_host(home)`。
协议 version=1：hello(clientId) → observation(sourceId,url,title,content,status,coverage,tabId,windowId) / ping / forget(sourceId)。
消息使用 native-endian uint32 长度前缀；单帧最多 512 KiB；origin 必须等于已登记的 Chrome 扩展来源。

验证：`app/.venv/bin/python -m pytest -q tests/test_dashboard_browser.py`；`node --check browser-extension/background.js`。
当前测试不读取私人页面、不安装桥接、不修改浏览器设置；用户须按上方流程完成真实浏览器授权验收。
