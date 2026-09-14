---
name: hi-other
version: 1.0.0
description: >-
  通过本机 hi CLI 处理 hi 内部 IM/工作台除消息外的其他操作：日历日程与会议室查询、共同空闲、通知与待跟进汇总。适用于"看我今天/本周日程""找共同空闲时间""约会议室""整理 hi 通知/提醒"等请求。默认只读；日程创建/修改/取消必须征得用户明确确认。
metadata: { 'openclaw': { 'requires': { 'bins': ["node", "hi"] } } }
---

# Hi 其他操作：日历、会议室与通知（不含消息、不含文档）

> 本 skill 是 `hi全家桶` 的"其他操作"子技能；消息检索用 `hi消息操作`，文档用 `hi文档操作/hi-docs`。

## 0. 安全与权限边界（必须遵守）

- `hi` CLI 使用当前机器 `~/.token/hi_token.json` 中的 OAuth 登录态，**权限等同于当前登录用户**，不具备额外权限。
- 默认只读；任何日程创建、修改或取消必须在执行前征得用户明确确认。
- 下列均属于**写操作**，即使 CLI 能执行，也必须先展示拟执行的对象/时间/参会人/影响并取得用户明确确认：
  - `calendar:create` / `calendar:create-recurring`
  - `calendar:edit-schedule` / `calendar:cancel-schedule`
  - `calendar:create-focus-time` / `calendar:edit-focus-time` / `calendar:cancel-focus-time`
  - 任何通过开放接口或 Provider 执行的发送消息、改群设置、加群成员等操作（本 skill 不提供消息写能力）
- 本 skill **不提供"已读/未读"状态同步**能力。可把"最新消息、@、系统提醒、审批提醒"等概括为"待跟进通知"，不要声称拿到了真实未读数。

## 1. 环境与身份检查

每个新的工作轮次先检查 CLI 与当前身份：

```bash
which hi
hi search:me
hi calendar:get-timezone
```

若 `hi` 不存在：

```bash
npm install -g @xhs/hi-cli --registry=http://npm.devops.xiaohongshu.com:7001
```

若提示 token 失效或授权异常，提示用户重新登录/授权；不要尝试读取、打印或上传 token 内容。

**所有具体命令都先运行 `--help` 核对参数，禁止凭记忆猜测。**

## 2. 日历、会议与空闲时间

### 2.1 只读能力（默认使用）

| 目标 | 命令 |
|---|---|
| 当前账号时区与本地时间 | `hi calendar:get-timezone` |
| 查询自己或他人日程 | `hi calendar:get-user-schedules` |
| 查询多人共同空闲 | `hi calendar:get-common-free-time` |
| 查询单个日程详情 | `hi calendar:get-schedule-detail` |
| 查询会议室区域 | `hi calendar:get-area-list` |
| 推荐空闲会议室 | `hi calendar:get-room-free-time` |
| 查询会议室占用 | `hi calendar:get-room-schedules` / `hi calendar:query-room-schedule` |
| 基于参会人推荐会议时间和会议室 | `hi calendar:get-intelligent-recommendation` |

### 2.2 看我的日程

先查时区，再查明确时间窗。单次查询时间窗最多 30 天；默认建议只查今天或未来 7 天。

```bash
hi calendar:get-timezone
hi calendar:get-user-schedules \
  --begin-time "2026-08-14T00:00:00+08:00" \
  --end-time "2026-08-14T23:59:59+08:00"
```

- `hasDetailPermission=false` 时，只能使用时间和创建人来判断忙闲，不要臆测会议标题或内容。
- 若 `hasMore=true`，根据用户请求继续翻页，直到 `false` 后再下"全天/全周"结论。

### 2.3 找共同空闲时间

```bash
hi calendar:get-common-free-time \
  --begin-time "2026-08-14T09:00:00+08:00" \
  --end-time "2026-08-14T18:00:00+08:00" \
  --account-id-list "a@xiaohongshu.com,b@xiaohongshu.com" \
  --duration 30
```

输出应明确：时间窗、参会人、最小时长、可用时段；不要仅说"有空/没空"。

### 2.4 日程写操作（必须确认）

用户请求新建、修改、取消日程时：

1. 先查 `hi calendar:<command> --help`；
2. 展示时间、时区、标题、参会人、会议室、循环规则和腾讯会议开关；
3. 明确询问："是否按上述信息执行？"；
4. 得到肯定答复后，先执行 `hi utils:generate-operate-code`，再执行对应写命令；
5. 返回 `scheduleId` 和最终时间。

## 3. 通知与待跟进事项（只读聚合）

当前 CLI 没有通用 `notification` 命令，因此通知采用以下**可解释来源**汇总；不要伪称"全量通知中心"或"真实未读数"。

### 3.1 可汇总来源

1. **系统/机器人会话消息**：例如 REDoc、Mipha、HR、审批机器人、日程机器人等，通过 `search:message` 按近期时间窗与关键词检索（消息检索细节见 `hi消息操作`）。
2. **@ 与提及提醒**：搜索"提及""@""申请编辑""待审批""提醒"等；结果是否可见依赖 hi 搜索索引与账号权限。
3. **今日日程与即将开始会议**：通过 `calendar:get-user-schedules` 汇总。
4. **任务清单（可选）**：可使用 `hi todos:list-tasks --task-scenario responsible` 查询待我处理任务；这不是通知中心，应单独标注为"任务"。

### 3.2 通知汇总流程

1. 先询问或默认限定到"今天"/"近 24 小时"。
2. 查当天日程，得到时间敏感事项。
3. 用 `search:message` 检索系统提醒相关关键词，按时间倒序。
4. 去重：同一提醒的重复推送只保留最新一条。
5. 按优先级输出：
   - **需立即处理**：审批、即将开始的会议、明确 @、截止时间今天
   - **今日跟进**：项目阻塞、排期、候选人、异常 case
   - **仅供知悉**：日报、订阅、资讯
6. 每项必须附上来源类型与时间；不确定时标为"需确认"，不要推断。

### 3.3 推荐输出模板

```markdown
## Hi 今日提醒（截至 HH:MM）

### 需立即处理
- [审批] 事项 — 来源：系统/会话，HH:MM

### 今日跟进
- [项目] 事项 — 最新消息：发送人，HH:MM

### 日程
- HH:MM–HH:MM｜会议标题/忙碌时段

### 仅供知悉
- [订阅/机器人] 摘要 — HH:MM
```

## 4. 常见请求与执行路由

| 用户意图 | 路由 |
|---|---|
| "看我今天/本周日程" | 时区 → `calendar:get-user-schedules` → 按时间排序 |
| "找我和某人的共同空闲" | 搜员工邮箱 → `calendar:get-common-free-time` |
| "帮我约个会/订会议室" | 先做空闲/会议室推荐；**创建前必须二次确认** |
| "整理今天 hi 的待处理事项" | 当日日程 + 系统/机器人消息 + @/审批关键词 → 按优先级去重汇总 |
| "查某群的聊天消息" | **切换到 `hi全家桶/hi消息操作`** |
| "读取/修改 hi 文档" | **切换到 `hi全家桶/hi文档操作/hi-docs`** |

## 5. 禁止事项

- 禁止任何 `hi docs:*`、`hi search:doc`、`hi search:file` 或 REDoc 文档链接读取。
- 禁止在无明确确认时创建、修改、取消日程或专注时间。
- 禁止通过 Provider/OpenAPI 绕过本 skill 的只读边界来发送消息、拉人、改群设置或执行其他写操作。
- 禁止将"搜索命中"表述为完整聊天历史、完整私聊列表、完整通知中心或真实未读状态。
- 禁止输出 token、cookie、完整附件 URL 或无关人员的敏感聊天内容。
