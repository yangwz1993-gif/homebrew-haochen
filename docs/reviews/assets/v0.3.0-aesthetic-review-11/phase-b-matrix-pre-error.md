# Round 11 — Phase B 发布门禁矩阵（错误态前停点）

- 实例未切换：App PID `18392`；引擎 PID `18405`。
- session-dir 仍为 `/private/tmp/haochen-qa-round11-valid.QFB1Sn/pi-sessions`。
- 未读取源码、测试、Git、roadmap、旧报告或旧截图。
- 当前状态：有效目录矩阵已完成，尚未切换无凭据目录，尚未执行最终错误 / Retry 复验。

## 1. 多行与键盘

**通过。**

- 桌宠输入框：输入“第一行”后按 `⌘Enter`，只插入换行，没有发送；继续补“第二行”“第三行”后按 `Enter` 才发送。
- 完整会话输入框同样通过。
- 两处发送后的历史用户气泡都逐行保留 `第一行 / 第二行 / 第三行`，没有合并成一行。
- 证据：[10-pet-cmd-enter-newline.jpg](./10-pet-cmd-enter-newline.jpg)、[11-pet-multiline-history.jpg](./11-pet-multiline-history.jpg)、[12-detail-cmd-enter-newline.jpg](./12-detail-cmd-enter-newline.jpg)、[13-detail-multiline-history.jpg](./13-detail-multiline-history.jpg)

## 2. “只回复好”与 transient 结果

**通过。**

- 处理中有明确状态；最终气泡显示可见“好”，带“继续问 / 查看详情”。
- 协调者提供的应用生命周期时间为 `06:16:11.508 → 06:16:31.533`，PRESENTING 共 `20.025 秒`，满足至少 15 秒且非完成即消失的 transient 门禁。
- 打开完整会话后，纯文本“好”位于绿色“结论”卡片，不是只有“依据与细节”。
- 证据：[14-short-good-result.jpg](./14-short-good-result.jpg)、[15-short-good-conclusion.jpg](./15-short-good-conclusion.jpg)

## 3. Dock 与各边缘

**气泡与人物可见性通过；Dock 同帧安全间距证据不足。**

- 将人物继续向底边以外拖动后，人物 App 截图仍保留完整 96×96 全身，没有被自身窗口裁掉。
- 底边输入态、处理中、结果态均把气泡置于人物上方，尾巴朝下指向人物；输入框、停止、继续问、详情都完整。
- 左缘尾巴切到左下，右缘为右下，顶部尾巴翻到上侧；人物肤色与动作在四边没有发生状态冲突。
- CUA 的 App 观察只裁切 App 窗口，无法把 macOS Dock 与人物放进同一帧；因此“距 Dock 的像素级安全间距”不能给强证据。
- 证据：[16-dock-input.jpg](./16-dock-input.jpg)、[17-dock-processing.jpg](./17-dock-processing.jpg)、[18-dock-result.jpg](./18-dock-result.jpg)，以及 Phase A 的边缘证据 05、06、09。

## 4. 三会话、标题、管理

**标题区分失败；入口找到；重命名 / 删除为证据不足。**

- 桌宠右键没有菜单。
- 菜单栏第二个“haochen”菜单提供“打开对话 / 设置… / 新会话”；“打开对话”会显示带侧栏的完整会话窗口。
- 通过菜单栏连续新建了 3 个会话，分别发送彩虹、两种咖啡、7 天 Python 计划三个不同首问，最后一个为长首问；三者均完成回答。
- 打开侧栏后，三个新会话标题全部仍显示为“新会话”；等待后仍未变化，无法区分，也没有出现可判断是否“二次改成突兀冒号”的有效标题。
- 侧栏会话行是自绘控件，未暴露为 CUA AX 元素；CUA 又没有 hover API，坐标右键返回 `AXError.notImplemented`，因此不能验证 hover 后的重命名 / 删除入口与删除确认安全性。
- 证据：[25-three-sessions-generic-titles.jpg](./25-three-sessions-generic-titles.jpg)、[26-three-sessions-still-generic.jpg](./26-three-sessions-still-generic.jpg)

## 5. 停止生成

**明显瑕疵，稳定复现。**

- 详情页长回答生成中点击“停止生成”后，按钮恢复为“发送”，但截断正文仍包装在普通“结论 / 依据与细节”卡片内，没有“已停止 / 回答未完成”标记；可见末句停在“它的方程说：物质能量决定时空的弯曲，时空”。
- 随后追问“刚才停在哪里”，回答错误地说“刚才我们聊到怎么快速开始一天的工作”，把更早话题当成刚才话题，历史状态指代失真。
- 桌宠侧停止会正确显示“已停止”“我停下来了”“查看已有内容”；但打开已有内容进入详情后，已停止标记丢失，半截内容仍呈普通完成卡片。
- 停止后可以继续追问，`Esc` 也能正常回到桌宠，没有死路。
- 证据：[19-detail-stop-unmarked.jpg](./19-detail-stop-unmarked.jpg)、[20-post-stop-history-wrong.jpg](./20-post-stop-history-wrong.jpg)、[21-pet-stop-marked.jpg](./21-pet-stop-marked.jpg)

## 6. 常规核心

**主要交互通过；历史准确性因停止后错指代失败。**

- 紧凑简答、详情、继续问、收起均可用。
- “最多两点”最终只给两点，没有越界扩写。
- 含糊问题“帮我看看这个”先澄清，没有乱用工具。
- 读屏请求展示明确的“读吧 / 不读”确认；选择“不读”后，工具历史准确显示“未执行 · 未获授权”，最终明确说明没有读屏、没有删除文件、不会假装做过。
- 详情窗口最大化后保持居中窄列，恢复正常。
- 但停止生成之后的“刚才停在哪里”错指代属于历史状态准确性失败，见第 5 项。
- 证据：[23-read-screen-refusal.jpg](./23-read-screen-refusal.jpg)、[24-maximized-detail-phase-b.jpg](./24-maximized-detail-phase-b.jpg)

## 错误态前结论

- 当前有效目录矩阵没有观察到启动或操作死路。
- 已确认的明显瑕疵：详情页停止生成缺少中止标记；停止后追问把更早话题误称为“刚才”；三个已回答新会话仍全为“新会话”，不可区分。
- 证据不足：Dock 同帧安全间距；会话行 hover 后的重命名 / 删除及确认安全性。
- 此处不做最终 PASS / NOT PASS，等待切换无凭据隔离目录后的错误 / Retry 复验。
