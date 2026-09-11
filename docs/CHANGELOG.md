# haochen CHANGELOG

> 本次发布：**v0.3.4**；上一公开稳定版：**v0.3.3**。
> 所有者已验收并确认本版为里程碑，继续沿用 legacy Homebrew 分发；它未经 Apple 公证。

---

## v0.3.4

- 轻量中性气泡与统一会话/首启/设置视觉，修正留白、边框、布局和人物锚点。
- 统一模型连接、凭据加载、会话恢复和模型就绪流程，修复初始化填写后不能立即聊天的问题。
- 每次提问重新识别阅读目标，固定原生页面/窗口/文档对象，真实文本和原图优先。
- 修复网页旧 URL 导致的身份误判；增加固定中、可切换、已读取提示，不把失败误说成用户关窗。
- 读取器常驻与免重复解包、同轮图片下载去重，不削减内容或图像质量。
- 471 项测试通过、覆盖率 83%。完整变更见根目录 CHANGELOG.md；[发布与回滚说明](releases/v0.3.4.md)。

---

## v0.3.2

- 短气泡新增小型关闭、发送和完整会话展开图标；展开时保留未发送草稿。
- 结果默认停留 20 秒，鼠标交互期间暂停自动退场，授权、错误和工作态仍不会误消失。
- `haochen` 固定为桌宠唯一英文名；旧版“阿晨”称呼必须由用户在冷启动中确认后才会注入长期档案。
- 首启向导和设置页均可配置自定义 OpenAI 兼容模型 URL、模型 ID、显示名称和 Key；Key 仅存 macOS Keychain。
- 自定义模型采用先验证后事务保存，失败不覆盖旧配置；公网 URL 强制 HTTPS 并拒绝 URL 内嵌凭据。
- 首启向导完成产品视觉和中文操作文案；设置页自定义模型表单默认收起，降低视觉密度。
- 当前质量基线：354 项测试通过、覆盖率 83%，分层回归 6/6。

---

## v0.3.1

- 修复桌宠输入编辑区越过气泡主体、压入尾巴区域的问题。
- 输入态改为更窄、更轻的漫画气泡，移除嵌套圆角框和常驻键盘说明，发送按钮同步减重。
- 一至三行输入随内容自动增高，第四行起框内滚动；普通位置、顶部翻转及内容与输入并存三类布局均增加可测量的主体内安全留白与自动回归。
- 修复首次问候和输入框并存时的大片空白，以及窗口销毁后的延迟滚动回调引发的 Qt 段错误。
- 待机、思考、警示三态升级为全身动作，同时保留 v0.3.0 的成年像素工程师特征与暖橙肤色。
- 姿态过渡始终保持 94% 以上不透明度，避免彩色桌面透过面部造成瞬时灰紫色肤色漂移。
- 当前质量基线：342 项测试通过、覆盖率 85%，分层回归 6/6。

---

## v0.3.0（发布候选）

> 状态：产品与体验候选已通过第 16 轮独立用户验收；按所有者选择使用稳定自签身份和
> Homebrew Cask quarantine 移除机制发布，并清楚披露未经 Apple 公证。

### 桌宠主交互

- 常驻大对话框改为人物旁的漫画式瞬时气泡；工作过程显示可理解的真实阶段反馈，完成后只保留简明结论。
- 结果提供“继续问”和“查看详情”，短结果自动退场；授权确认和错误状态不会被自动隐藏。
- 首次提示在用户实际点击后才完成，单击和右键入口可发现；支持 Reduce Motion。
- 保留原有成年像素工程师形象与肤色，修正 Retina 渲染、人物裁切、尾巴锚点和气泡留白。

### 回答与完整会话

- 单次模型回合同时产生简答与详情，桌面只展示说人话的结论，完整依据在详情页展开。
- 修复 Markdown 裸标记、短会话底部堆积、详情展开竞态、停止后旧任务污染下一轮等问题。
- 冷启动从 JSONL 重建历史侧栏；有效会话、失败回合和中文错误信息均可跨重启恢复。
- 完整窗口补齐键盘入口、设置页 Esc、可读模型别名和读屏拒绝路径。

### 稳定性、安全与交付

- 引擎生命周期、自动恢复、读屏权限时序、Keychain 状态和会话损坏恢复均增加回归覆盖。
- 第 16 轮独立验收按盲测方式逐状态真实操作、截图与复启，最终结论 PASS。
- 最终 legacy 包第 19 轮独立验收连续三次确认详情从会话顶部打开，真实问答与上下文追问均 PASS。
- 当前质量基线：336 项测试通过、覆盖率 85%，分层回归 6/6。
- 默认正式构建仍强制 Developer ID 与公证；v0.3.0 经所有者明确授权走独立 legacy 门禁，开发 ad-hoc 产物仍不能进入发布渠道。

---

## v0.1.10（2026-08-30）

> 状态：构建完成，待产品/用户终审。产物 `packaging/dist/haochen-0.1.10.dmg`。
> **签名：haochen Local Signing（稳定身份 662F89A7...，证书不变 → 升级免重授权）**。
> 依据：`docs/v0110-todo.md`（详情答案 markdown 露原形：`==/answer==`/`==高亮==`/`===标题===` 原样显示）。

### 详情答案 markdown 渲染修复（方案 1+2，不动渲染器）
- **根因**（排查定论）：模型标记输出漂移（`【answer】` 开头却 `==/answer==` 收尾 + 滥用 `==高亮==`）+ 解析层只认【】、兜底不剥 `==` + Qt setMarkdown 不认 `==`。
- **解析层加固**（`conversation.py`）：`_ANSWER_PAIR`/`_SUMMARY_PAIR` 正则兼容错误闭合（`==answer==`/`==/answer==` 等变体视同【】系）；`_ANY_TAG` 扩展 → answer 兜底 `strip_tags` 同步剥净 `==` 系整标记（与 summary 清洗对齐）。
- **渲染前清洗**（`chat/widgets.py` `MarkdownView.set_markdown` 前置 `_sanitize_markdown`）：`==文字==` → `**文字**`（Qt 认的粗体，Qt 实际渲染为 font-weight:700 span）；`===文字===` → `**文字**`；残留 `==协议标记==` 剥净。纯兜底、无新依赖、不换渲染器。
- **提示层收紧**（`ext/index.ts` ANSWER_RULES/SUMMARY_RULES）：闭合标记只能用【/answer】【/summary】，禁止 `==/answer==` 等号写法；`==文字==` 仅限正文关键词少量高亮，不得用于分节/闭合。

### 验证
- 新增 `chat/verification/test_markdown_sanitize.py`（11 断言：混合/纯等号错误闭合解析、strip_tags 剥净、`==高亮==`/`===标题===` 转换、端到端重放真机式内容→HTML 无裸标记），并入回归 L1 项。
- 全量回归 `tests/run_all_regressions.sh --real` → **PASS=6 FAIL=1**（L1 45/45+21/21+11/11、L2 26/26、M-C、M-E、P4 25/25、L4 打包全过；**L3.5 真链路因 API 402 Insufficient Balance（DeepSeek 账户余额不足）无法运行**——非代码问题，充值后需复跑到 PASS=7）。
- 打包链：`codesign -dvvv` → `Authority=haochen Local Signing`，`CFBundleShortVersionString=0.1.10`；v0.1.9 dmg 已归档 `packaging/dist/archive/`。
- 分发：homebrew-haochen 已 push（cask 0.1.10 + tag v0.1.10）；release dmg 资产由用户网页上传。
- 未覆盖：真机复验（模型是否按收紧后的提示正确闭合）需观察。

---

## v0.1.9（2026-08-29）

> 状态：已归档 `packaging/dist/archive/haochen-0.1.9.dmg`。
> **签名：haochen Local Signing（稳定身份 662F89A7...，证书不变 → 升级免重授权）**。
> 依据：`docs/v019-todo.md`（真机复验：看图模式已触发但智谱 API 400 unsupported image）。

### 看图 400 修复（根因：损坏占位图混进发送队列）
- **根因定论**（真引擎 + 本地 mock server 抓包实证）：发送结构**本就符合** OpenAI 视觉规范（`image_url` + data URL 纯 base64，引擎转换层无问题）；真凶是 reader 下载的 13 张图里混了 1 张 **78 字节截断 PNG**（占位图，无 IDAT/IEND），智谱报错路径 `.messages[4].image[0]` 精确对应它。
- **发送前校验**（`ext/index.ts` `parseDataImage()`）：严格 base64 校验 + 解码 + 按 mime 查 magic/完整性（PNG 必须签名+完整 IEND；JPEG/GIF/WebP 同理），损坏图剔除不上送（`details.imagesDropped` 计数），仅放行 png/jpeg/gif/webp。
- **reader 加固**（`reader/haochen_reader.py`）：下载图用 PIL 实际解码把关、mime 以内容为准；截图 >4MB 等比压缩长边 ≤2000px（仍超逐半降档，下限 800px），坏图宁缺毋滥。
- 引擎转换层未动、未重建。

### 验证
- 抓包复验（修前/修后 wire payload）：修后 messages[4] 严格合规，截断图未上送。
- `app/ext/test-visual-mode.ts` 15 → **21 断言**（新增：截断 PNG 被过滤、好图照发、纯 base64+PNG 头、截图损坏不上送）。
- 全量回归 `tests/run_all_regressions.sh --real` → **PASS=7 FAIL=0**（L1 45/45+21/21、L2 26/26、M-C、M-E、P4 25/25、真链路 16/16、L4 打包）。
- 打包链：`codesign -dvvv` → `Authority=haochen Local Signing`，`CFBundleShortVersionString=0.1.9`；v0.1.8 dmg 已归档 `packaging/dist/archive/`。
- 分发：homebrew-haochen 已 push（cask 0.1.9 + tag v0.1.9）；release dmg 资产由用户网页上传。
- 未覆盖：真机微信图片窗口问图复验（若仍 400 需再抓包看是否撞单请求图片数上限）。

---

## v0.1.8（2026-08-27）

> 状态：已归档 `packaging/dist/archive/haochen-0.1.8.dmg`。
> **签名：haochen Local Signing（稳定身份 662F89A7...，与 v0.1.6/0.1.7 同证书 → 升级不清 TCC、不要求重新授权）**。
> 依据：`docs/v018-todo.md`（真机反馈：问图读不了图，答非所问）。

### 看图模式（图片问题 → 截屏发多模态模型）
- **根因**：read_screen 只走 AX 文字读屏，图片在 AX 树里只有占位无文字；且 `ext/index.ts` 在 blocks 为空时 early-return「没有可读内容」——即使 reader 已截到图也不发给模型。
- **看图模式判定**（`ext/index.ts`，满足任一即触发）：① 用户问题含看图意图（读 `HAOCHEN_HOME/last-user-text.txt`，关键词：看图/这张图/图里/图片/照片/帅/美/评价一下/长什么样/image/photo 等；壳侧 PetApp/ChatWindow 发送时落盘用户原文）；② 前台窗口是图片/视频/预览类（标题/app 名匹配）；③ AX blocks 为空或文本极少（<80 字符）。
- **截图发图**：看图模式下截图 PNG 直接作为图像输入发模型（不再要求 nImg==0）；blocks 空但有截图时返回截图+简述（修掉 early-return）。有 URL 原图仍走 P8 原图优先策略（不动）。纯文字问题行为不变，不滥用截屏。结果带 `details.visualMode` 供断言。
- **屏幕录制未授权**：看图模式拿不到截图 → `isError` + 明确文案「需要屏幕录制权限才能看图」+ `details.needScreenRecording`，走现有三层授权引导，不静默失败。
- reader 未动：blocks 空 ⇒ 无 URL 原图 ⇒ 现有逻辑自动截图，无需新参数。

### 验证
- 新增 `app/ext/test-visual-mode.ts`（Bun 直跑 + 假 reader，15 断言：看图意图带图/稀疏文本触发/纯文字不截图/无 SR 权限报错/空窗兜底），并入回归 L1 项。
- 全量回归 `tests/run_all_regressions.sh --real` → **PASS=7 FAIL=0**（L1 45/45+15/15、L2 26/26、M-C、M-E、P4 25/25、真链路 16/16、L4 打包）。
- 打包链：`codesign -dvvv` → `Authority=haochen Local Signing`，`CFBundleShortVersionString=0.1.8`；v0.1.7 dmg 已归档 `packaging/dist/archive/`。
- **顺带修复**（回归暴露的段错误）：`chat/widgets.py` `fade_in()` 动画 finished 槽的 wrapper 引用岛被 GC 回收导致崩溃——动画存活期间用模块级集合根住，槽内自行摘除。
- 分发：homebrew-haochen 已 push（`Casks/haochen.rb` → 0.1.8 + 新 sha256，tag v0.1.8）；**GitHub Release 资产上传待补**（本机无 gh/API token，需 `gh auth login` 后 `gh release create v0.1.8 packaging/dist/haochen-0.1.8.dmg --repo yangwz1993-gif/homebrew-haochen`）。
- 未覆盖：微信图片窗口真机问图（验收点）需装机人工确认。

---

## v0.1.7（2026-08-26）

> 状态：已归档 `packaging/dist/archive/haochen-0.1.7.dmg`。
> **签名：haochen Local Signing（稳定身份 662F89A7...，与 v0.1.6 同证书 → 升级不清 TCC、不要求重新授权）**。
> 依据：`docs/v017-todo.md`（v0.1.6 基础上 6 点体验优化）。

### 1) 闲聊问候更自然亲切
- `ext/index.ts` ANSWER_RULES 加口吻规则：闲聊问候像朋友一样自然口语化、一两句即可，禁客服腔/机械列点；技术问题保持"有帮助的技术专家"风格（像素眼镜小哥人格）。

### 2) 详情页框线/配色（仅大窗，小气泡保持去框线现状）
- 详情页 bot 回答气泡/工具卡加回清晰框线（1px ink，圆角保留）；详情页用户气泡改浅绿 `accent_tint #e4efe8`（与小窗一致）。

### 3) 读屏确认支持回车
- pet：确认条弹出即聚焦「读吧」；确认条挂起期间输入框回车拦截为「确认读吧」（已输入文字保留，Esc=收起语义不变）。chat：ConfirmBar 接管焦点 + Return/Enter 确认。

### 4) 首次打开询问称呼并记住
- 新模块 `pet/profile.py`（`user-profile.json`）。首次唤起气泡问候「第一次见面～怎么称呼你？」；输入称呼落盘（`≤16字无句读`判别，不像称呼当正常提问放行；「算了」跳过）；此后问候/引擎提示词（ext 注入「用户希望被称呼为 X」）使用该称呼，重启不丢。

### 5) 默认气泡与人物连在一起
- 根因：summon 动画终点按唤起前过期高度锚定，内容撑高后被拉回旧位。修复：summon/锚定前先刷新高度，动画结束兜底重锚定——启动后首次唤起即在人物正上方（间隙 8px、尾巴对中）。

### 6) 感知提示单行
- 「👀 我正看一下你的屏幕…」HintBlock 禁折行（`setWordWrap(False)`，文案未动），400px 气泡内单行完整。

### 验证
- 全量回归 `tests/run_all_regressions.sh --real` → **PASS=7 FAIL=0**（L1 45/45、L2 26/26、M-C chat 全场景（含新增回车确认/浅绿气泡/框线断言）、M-E pet 56 断言（含新 S0 问称呼/锚定/单行/回车确认）、P4 25/25、真链路 16/16、L4 打包）。
- 打包链：`codesign -dvvv` → `Authority=haochen Local Signing`，`CFBundleShortVersionString=0.1.7`；v0.1.6 dmg 已归档 `packaging/dist/archive/`。
- 截图目检：称呼问候卡自然亲切、称呼被记住并使用；感知提示单行；详情页框线清晰+用户浅绿；「读吧」醒目绿。
- 未覆盖：真机端到端（回车手感、升级免重授权）需装机人工过一遍。

---

## v0.1.6（2026-08-26）

> 状态：已归档 `packaging/dist/archive/haochen-0.1.6.dmg`。
> **签名：haochen Local Signing（稳定身份 662F89A7...）**。
> 依据：`docs/v016-todo.md`（v0.1.5 真机反馈 3 问题：确认按钮浅白 / 文本大框套小框 / 气泡压人物）。

### 签名说明（重要）
- v016 要求恢复旧证书 `87E97858`（备份钥匙串 `haochen-signing.keychain-db.bak-20260826`）。**无法恢复**：该钥匙串被锁且随机密码文件早已删除（空密码/新钥匙串密码/shell 历史/Trash/旧 pem 均尝试无果），私钥不可导出 → 旧证书对应的 DR 无法再签出，87E97858 作废。
- 本版继续用 v0.1.5 重建的身份 `662F89A7`。**为达成"避免重新授权"的实际目标**：构建指纹改为**只按签名证书**（不再含版本号，`permissions.py` `build_fingerprint()`）——证书不变时升级**不清 TCC 记录、不要求重新授权**；从 v0.1.5 升级的用户授权直接保留。仅证书变更才清记录重授（假蓝根治路径不变）。

### 1) 读屏确认「读吧」改回醒目绿色
- pet/chat 两处 ConfirmBar 允许按钮：品牌绿 `#3a7d5c` 底 + 白字加粗 + hover `#2f6a4d`，高对比；「不读」中性次要（灰字透明底）。

### 2) 小窗文本去"大框套小框"丑边框
- 气泡与对话窗的消息块/工具卡/输入框**全部去描边**，仅用背景色区分（回答淡米白卡片、用户消息浅绿 tint `#e4efe8`），圆角保留；输入框 focus 时才亮 1px accent。确认条/错误条保留 1px 语义色边；气泡外圈自绘主描边保留。

### 3) 气泡固定人物正上方 + 联动拖动 + 位置记忆
- **定位修正**：气泡恒在人物正上方、间隙 8px（`BUBBLE_PET_GAP`），内容增高时经 `BubbleWindow.resized` 信号重新锚定，不再压人物（根因：`setFixedHeight` 向下长且只在唤起时定位一次）。
- **联动拖动**：拖人物 → 气泡实时跟随（`PetWindow.moved`）；拖气泡 → 人物跟到尾巴尖正下方。关系恒为"气泡在人物正上方"。
- **位置记忆**：拖动结束写 `HAOCHEN_HOME/pet-pos.json`，启动恢复（屏幕可用区校验，出界回退右下角）。

### 验证
- 全量回归 `tests/run_all_regressions.sh --real` → **PASS=7 FAIL=0**（L1 45/45、L2 26/26、M-C 5/5、M-E pet 45 断言（含新 S8：气泡不压人物/双向联动拖动/位置记忆恢复；S2 新增「读吧」绿色断言）、P4 25/25、真链路 16/16、L4 打包）。
- 打包链：`codesign -dvvv` → `Authority=haochen Local Signing`，`CFBundleShortVersionString=0.1.6`；v0.1.5 dmg 已归档 `packaging/dist/archive/`。
- 截图目检：「读吧」醒目绿白字；消息区无框线大框套小框；气泡在人物正上方不重叠。
- 未覆盖：真机端到端（拖动手感、升级后授权保留）需装机人工过一遍。

---

## v0.1.5（2026-08-26）

> 状态：已归档 `packaging/dist/archive/haochen-0.1.5.dmg`。
> **签名：haochen Local Signing（稳定身份 662F89A7...，见下方「签名身份重建」说明）**。
> 依据：`docs/v014-hotfix-todo.md`（v0.1.4 真机反馈 4 问题：未授权读屏 / 小窗灰底+框线 / 详情页回退 / Esc 退 app）。

### 签名身份重建（重要说明）
- 构建中发现签名钥匙串被系统锁定且**密码文件已丢失**（旧 setup 未保留 `signing.keychain-pw`），codesign 挂死无法签名 → 重建了同名身份 `haochen Local Signing`（新证书 SHA-1 `662F89A7...`，旧 `87E97858...` 作废，旧钥匙串备份为 `haochen-signing.keychain-db.bak-20260826`）。
- 后果：**DR 变一次 → 老用户本版需重授一次权限**——与本次「构建指纹清旧 TCC + 重授」设计正好一致（指纹含证书哈希，升级首启自动清旧记录并弹系统授权）。此后新身份密码已存 `packaging/signing.keychain-pw`（chmod 600）+ 钥匙串禁止自动锁定，DR 跨构建重新稳定。
- `build.sh` 签名段加固：构建前用密码文件显式解锁钥匙串；身份探测由 `find-identity -v`（会过滤未受信自签证书，误判走 ad-hoc）改为 `find-certificate -Z`；`setup-signing.sh` 补 `basicConstraints=CA:FALSE` + 禁自动锁定。

### 1) 未授权不得读屏 + 清理历史 TCC（三层防护）
- **根因**：稳定签名使 DR 跨构建固定，旧版本授过的 TCC 记录对新构建仍有效 → 用户未对本版授权却能读屏。
- **构建指纹清旧记录**（`permissions.py` `build_fingerprint()` / `reconcile_tcc_with_build()`，`app_shell.start()` 接入）：指纹 = `CFBundleShortVersionString` + 签名 leaf 证书 SHA-1，存 `HAOCHEN_HOME/build-fingerprint`；升级/重装/换签名首启 → 先 `tccutil reset` 清本 app 的 Accessibility+ScreenCapture 旧记录 → 现有引导流程自然触发系统授权弹窗。同版本同签名重启不清（授权持久）。mock/测试环境跳过，不碰真实 TCC。
- **引擎扩展前置校验**（`ext/index.ts`）：`read_screen` 在用户确认后、真正读取前跑 `haochen-reader --check`；未授权 → `isError` + 「屏幕读取权限未授权……授权后重试」+ `details.permissionDenied`，绝不真正读屏（执行体缺失/不识别 --check 时放行给读取层自行报错，兼容开发态）。
- **执行体硬门控**（`reader/haochen_reader.py`）：`--json` 读取前自查 `AXIsProcessTrustedWithOptions`，未授权 → 报错退出码 2，绝不读屏；`--check` 输出 JSON 权限状态。

### 2) 小窗彻底去灰底 + 去深色会话框线
- **灰底根因**：`WA_TranslucentBackground` 下滚动区 viewport / flow_host / 输入区等中间容器默认填系统底色，盖住自绘米白底。`bubble.py` 全部中间容器显式 `background: transparent`。
- **去框线**：chat 气泡/工具卡/输入框 2px 深描边 → 1px `line_soft` 浅描边；确认条/错误条保留语义色降为 1px；气泡外圈自绘主描边保留（气泡本体轮廓）。

### 3) 详情页回退「大而清晰」+ 保留展开动画
- 废弃 v0.1.4 的 560×520 小详情窗（`pet/detail.py` 已删除）。「展开详细」改为打开 `ChatWindow` 的「从气泡展开」模式（`chat/window.py` `open_from_bubble(source_rect)`）：从气泡 rect 平滑扩展（geometry，OutCubic 220ms）到 1200×800（气泡中心锚定、夹回屏幕），展开后滚动**定位到当前轮**；「详答(L2)上、结论(L1)下」顺序保留，结论气泡排版清晰。
- 接线：壳层 `pet.detail_opener = chat.open_from_bubble` + `chat.detail_collapsed.connect(pet.restore_bubble)`（未注入时按钮空操作记 warning）。

### 4) Esc 退 app 严重 bug 修复
- 详情模式下 Esc **只收起**（优先于「打断生成」），⌘W / closeEvent 同样只收起；收起链只走「动画 → hide → detail_collapsed → 恢复气泡」，无任何 quit 路径。`ChatWindow` 增设 `WA_QuitOnClose=False`；动画 `stop()` 对 DeleteWhenStopped 后的旧对象加 RuntimeError 防护（v0.1.4 `pet/detail.py` 疑似崩溃点）。
- ⌘Q / 右键「退出」链路（`pet_window` → `quit_requested` → `PetApp.quit`）未动，正常退出保持可用。

### 验证
- 全量回归 `tests/run_all_regressions.sh --real` → **PASS=7 FAIL=0**（L1 45/45、L2 26/26、M-C 5/5、M-E pet 32 断言（含新增 S6/S7：Esc / ⌘W / closeEvent 收起后 app 未退出、窗口隐藏、气泡恢复）、P4 25/25、真链路 16/16、L4 打包）。
- 打包链：`codesign -dvvv` → `Authority=haochen Local Signing`，`CFBundleShortVersionString=0.1.5`；v0.1.4 dmg 已归档 `packaging/dist/archive/`。
- 截图目检：气泡米白无灰底、无深色会话框线；详情展开为大窗口样式。
- 指纹机制单测 5 场景全过（无指纹→重置 / 一致→不动 / 版本变更→重置 / 证书变更→重置 / 开发态回退）。
- 未覆盖：真机端到端（升级后旧授权被清+重授、Esc 手感）需装机人工过一遍。

---

## v0.1.4（2026-08-26）

> 状态：已归档 `packaging/dist/archive/haochen-0.1.4.dmg`（真机反馈 4 问题 → v0.1.5 修复）。
> **签名：haochen Local Signing（稳定身份 87E97858...）—— DR 固定，授权跨构建持久**。
> 依据：`docs/v014-todo.md`（体验优化 §0 授权简化 / §1 详情页 / §2 小窗 UI）。

### §0 初始授权流程简化
- **去掉产品自己的黄色授权引导卡片**：`permissions.py` 删除 `ensure_accessibility`/旧 `ensure_permissions` 两个 QMessageBox 引导卡（去授权/我已授权/暂不），重写为：检测到未授权 → **直接触发 macOS 系统授权弹窗**（`AXIsProcessTrustedWithOptions(prompt:true)` / `CGRequestScreenCaptureAccess()`），并同时打开对应系统设置面板（覆盖"已拒绝后系统不再弹"的系统限定）。
- **保留「重置重授」**（假蓝根治路径）：新增 QTimer 轮询（1.5s）授权状态，90s 超时仍未授权 → 弹简洁「重置重授/暂不」提示，重置走 `tccutil reset` 清本 app 旧记录 + 重弹系统授权后继续轮询。
- **授权完成后提示/自动重启**：全部授权检测到后，若屏幕录制为本次新授权 → 弹「权限已就绪，屏幕录制需重启生效」（立即重启/稍后）；`relaunch_app()` 用 detached `open -n <app>` 自动重开（开发态只记日志不重启）。
- 首启触发条件由"仅辅助功能未授权"放宽为"辅助功能或屏幕录制任一未授权"（`app_shell.py` `_guide_permissions`）；读屏时再授权走同一套。ad-hoc「一键修复签名」链路不变。

### §1 详情页
- **a) 结论后置**：对话流由"结论先行"反转为**先详答(L2)、后结论(L1)**——`chat/window.py` `_on_summary_done` 与历史渲染 `_render_history_assistant` 均不再前插 summary，结论气泡落在详答之后；`docs/interaction-spec.md` 相关原则/验收项同步更新。
- **b) 「展开详细」跳转详情窗口**：新建 `pet/detail.py` `DetailWindow`，展示被点击回答的完整内容（详答全文在上、结论卡在下）；`PetApp._on_expand_detail` 改为 pet 侧直出详情窗（不再经 AppShell 拉开 ChatWindow，`app_shell.py` 旧接线已删）。
- **c) 详情窗交互**：⌘W 关闭、Esc 收起（`QShortcut`，WindowShortcut 上下文）；打开/收起均为 `QPropertyAnimation`（geometry，OutCubic 220ms）从短会话气泡 rect 平滑扩展/收回，气泡随动画隐藏/复原。

### §2 小窗 UI 优化
- 消息区卡片化：对话窗口回答气泡底色 → `color-surface #fffaf0` 卡片浮于 `color-bg #fdf6e3` 米白底；气泡 SummaryBlock/ConfirmBar 圆角统一 16px；气泡输入框底色 `#fff` → `#fffaf0`（消除与米白反差）。
- 原生 QMessageBox 去系统灰：chat/settings 两处 QSS 补 QMessageBox 段（米白底 + ink 文字 + 卡片按钮），权限/确认等系统样式对话框统一为米白卡片风。
- 三套 theme token 逐一核对，已与 `docs/visual-spec.md` §1 完全一致。

### 验证
- 全量回归 `tests/run_all_regressions.sh --real` → **PASS=7 FAIL=0**（L1 45/45、L2 26/26、M-C 5/5、M-E 17/17、P4 25/25、真链路 16/16、L4 打包）。
- 打包链：`codesign -dvvv` → `Authority=haochen Local Signing`，`CFBundleShortVersionString=0.1.4`；旧版 0.1.2/0.1.3 已归档 `packaging/dist/archive/`。
- 详情窗冒烟：动画源 rect==气泡 geometry、详答/结论卡内容正确、Esc/⌘W 收起后气泡复原；chat 场景截图目检确认顺序为 详答→结论。
- 未覆盖：真机上系统授权弹窗/假蓝重置/自动重启的端到端行为需装机后人工过一遍（无头环境无法复现）。

---

## v0.1.3（2026-08-26）

> 状态：已归档 `packaging/dist/archive/haochen-0.1.3.dmg`。
> （当时漏记本文件，依据 `docs/v013-todo.md` 补录）

- **权限"假蓝"根治**（`permissions.py`）：签名身份重建后旧 TCC 记录 DR 与当前二进制不符 → 开关蓝但检测未授权。新增 `reset_tcc_records()`（`tccutil reset` 本 app 的 Accessibility+ScreenCapture，无需管理员）+ `_reset_and_reprompt()`，引导弹窗加「重置重授」入口。
- 无边框窗口/气泡支持拖动摆放。

---

## v0.1.2（2026-08-26）

> 状态：已归档 `packaging/dist/archive/haochen-0.1.2.dmg`。
> **签名：haochen Local Signing（稳定身份 87E97858...）—— DR 固定，授权跨构建持久**（根治 ad-hoc 反复授权）。

### 签名（关键，根治反复授权）
- `packaging/build.sh` 签名改为**默认用稳定身份**（`haochen Local Signing`，身份 SHA-1 + `--keychain` 签名），不再依赖 `packaging/signing.keychain-pw`（该文件不存在致走 adhoc）。无稳定身份才回落 ad-hoc。
- v0.1.2 产物 `Authority=haochen Local Signing`，DR 固定 → 辅助功能/屏幕录制授权**一次即持久**，不再因重建反复授权。

### 修复
- **权限检测**：`permissions.py` 加固——「我已授权」改为**轮询检测**（TCC 授权注册异步，避免"开关已开仍检测不到/弹窗不关"）；检测函数异常不再静默吞掉（记录真实原因）；未授权时给出「可能是旧签名，开关先关再开一次/重启」的明确引导。
- **打包/安装**：`packaging/build.sh` 版本号可配置（`HAOCHEN_VERSION`，默认 0.1.2），dmg 名与 Info.plist `CFBundleShortVersionString` **一致**（此前产品手动重打导致 dmg=0.1.1 但 plist=0.1.0 不一致）；由研发用 build.sh 正式构建，产物为完整可识别 app（签名 verify OK、资源/图标齐全、LaunchServices 识别）。
- **干净安装首启报"授信失败"**：修复 `app_bundle()` 返回 Contents（错）→ 改返回 `.app` 根（否则 `is_stable_signed` 误判 ad-hoc 触发授信读不存在 pem）；`_generate_identity` 在钥匙串已有身份时返回 `cert=None`（不再读可能不存在的 pem）。首启不再弹"授信失败"。
- **app_tracking**：NSWorkspace 激活监听改挂 `notificationCenter`（原在 workspace 对象上报 WARNING），sidecar 能记录用户最近激活窗口。

### 验证
- 全量回归 `tests/run_all_regressions.sh --real` → L1 45/45、L2 26/26、M-C 5/5、M-E 17/17、P4 25/25、真链路 16/16、L4 打包（**PASS=7 FAIL=0**；L3.5 单次瞬态 flake 已复跑确认非缺陷）。
- 安装在 `/Applications` 的 app：主进程/引擎子进程启动正常、资源正确解析、LaunchServices 识别（mdfind 命中）、图标渲染为像素小哥、干净安装首启无"授信失败"。

### 已知说明
- **LSUIElement=true**（DoD #6 无 Dock 图标/桌宠定位）：app 在 Finder 的 Applications 可见、可作为正式 app 启动，但 **Launchpad 默认不显示**（macOS 对 accessory/agent 应用不列 Launchpad）——属定位使然，非打包缺陷。若需 Launchpad 可见需去掉 LSUIElement（会显示 Dock 图标），产品若确要请指明。
- ad-hoc 签名（TeamIdentifier=not set）：本机/内测可用；**外发/他人安装需 Developer ID + 公证**（总纲 §五 既定）。

---

## v0.1.1（2026-08-25）

> 状态：已归档 `packaging/dist/archive/haochen-0.1.1.dmg`。

- 修复读屏视觉增强（P7）：截图 + 原图优先喂图 + 面积优先级；屏幕录制权限引导。
- 修复菜单窗口选择、气泡动态对话流、窗口尺寸、App 图标（像素小哥）等（对应 P6/P7 打回项）。
- 注：v0.1.1 因产品手动重打 dmg，出现 dmg=0.1.1 而 plist=0.1.0 的版本不一致，已在 v0.1.2 通过 build.sh 修正。

---

## v0.1.0（2026-08-25）

> 状态：已归档 `packaging/dist/archive/haochen-0.1.0.dmg`。

- P0–P6 全部里程碑完成：Bun 单文件引擎、契约冻结、三 UI、集成（真引擎全链路）、打包 dmg、回归打磨。
- 产品终审判定"可交付上市"。
