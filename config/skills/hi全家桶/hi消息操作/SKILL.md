---
name: hi-message
version: 1.0.0
description: >-
  通过本机 hi CLI 检索、整理并受控发送 hi 内部 IM 私聊/群聊消息。适用于“查 hi 群聊/私聊消息”“找最新消息/待跟进”“给某人或某群发消息”等请求。发送前须完成收件人消歧，并遵守写操作授权与结果校验规则。
metadata: { 'openclaw': { 'requires': { 'bins': ["node", "hi"] } } }
---

# Hi 消息操作：私聊 / 群聊 / 消息检索与发送

> 本 skill 是 `hi全家桶` 的消息子技能，只管 IM 会话与消息；文档类任务用 `hi文档操作/hi-docs`，日历/通知类任务用 `hi其他操作`。

## 0. 安全与权限边界（必须遵守）

- `hi` CLI 使用当前机器 `~/.token/hi_token.json` 中的 OAuth 登录态，**权限等同于当前登录用户**，不具备额外权限。
- 默认只做**最小范围、最短时间窗、最少结果量**的读取；不要无目的扫描所有会话或拉取大范围历史消息。
- 查询消息时，优先限定：关键词、`chat-id`、成员、发送人和时间范围；默认只看近 7 天，除非用户明确要求更长历史。
- 输出时只保留与用户目标相关的结论、时间、负责人、行动项；避免逐字转述无关私聊内容，避免暴露不相关人员的敏感信息。
- 不下载、转发或外发聊天里的图片、文件、链接或附件；如用户明确要求处理附件，应先说明附件内容不在本 skill 范围内。
- **受控开放发送消息**：仅允许发送用户明确要求的私聊或群聊消息；禁止擅自改写正文、扩大收件人范围、改群设置、加群成员或拉人。
- 当前请求若已明确给出收件人和正文，即视为对本次发送的授权；如仅表达发送意图但正文或收件人不明确，必须先补齐信息。多个候选联系人或群聊必须先让用户确认，禁止猜测。
- 发送属于外部可见写操作。调用前必须展示或在执行记录中明确最终收件人和正文；发送失败或结果不明确时禁止盲目重试，先查询状态或向用户报告。
- 本 skill **不提供"已读/未读"状态同步**能力。可把"最新消息、@、系统提醒、审批提醒"等概括为"待跟进通知"，不要声称拿到了真实未读数。

## 1. 环境与身份检查

每个新的工作轮次先检查 CLI 与当前身份：

```bash
which hi
hi search:me
```

若 `hi` 不存在：

```bash
npm install -g @xhs/hi-cli --registry=http://npm.devops.xiaohongshu.com:7001
```

若提示 token 失效或授权异常，提示用户重新登录/授权；不要尝试读取、打印或上传 token 内容。

**所有具体命令都先运行 `--help` 核对参数，禁止凭记忆猜测。**

## 2. IM：私聊、群聊与消息检索（只读）

### 2.1 核心命令

| 目标 | 命令 | 说明 |
|---|---|---|
| 搜索群聊 | `hi search:chat` | 按群名称关键词找到群聊及 `chatId`；主要用于群聊，不保证覆盖私聊 |
| 搜索消息 | `hi search:message` | 跨会话检索命中消息；可按会话、成员、发送人、关键词、时间窗过滤 |
| 查询当前身份 | `hi search:me` | 获取当前登录用户邮箱/contactId |
| 查询员工 | `hi search:employee` | 根据邮箱、姓名或薯名定位人员；先看 `--help` |
| 查询会话接口元数据 | `hi openapi:api-detail --alias ...` | 仅查询 API 结构，不直接调用写接口 |

### 2.2 会话与消息接口参考

若需要解释 hi 的底层会话能力，可用开放接口元数据确认：

| 接口 | 作用 |
|---|---|
| `im:chat.pageQueryRecentChat:v1` | 分页查询最近 chat（会话列表） |
| `im:chat.queryGroupChat:v1` | 按 `chatId` 查询群聊信息 |
| `im:chat.pageQueryChatMember:v1/v2` | 分页查询群成员 |
| `im:message.pageQueryMessageBySeq:v1` | 按 `chatId` 与消息序列分页查询消息 |

查询格式示例：

```bash
hi openapi:api-detail --alias "im:chat.pageQueryRecentChat:v1"
hi openapi:api-detail --alias "im:message.pageQueryMessageBySeq:v1"
```

### 2.3 标准检索流程

1. 确定用户目标：事项/项目/人员/时间范围。
2. 使用 `hi search:chat --help` 和 `hi search:message --help` 核对参数。
3. **先找群，再搜消息**：

```bash
hi search:chat --query "项目关键词" --page-size 20
hi search:message \
  --chat-ids "CHAT_xxx" \
  --message-send-time-stamp-start <开始毫秒时间戳> \
  --message-send-time-stamp-end <结束毫秒时间戳> \
  --page-size 30
```

4. 若群名不确定，使用关键词跨会话搜索；仍应设定时间窗：

```bash
hi search:message \
  --query "关键词A;关键词B" \
  --message-send-time-stamp-start <开始毫秒时间戳> \
  --message-send-time-stamp-end <结束毫秒时间戳> \
  --page-size 30
```

5. 若围绕某位人员，优先限制发送人或会话成员（**私聊查法**）：

```bash
hi search:message \
  --message-send-account-ids "name@xiaohongshu.com" \
  --message-send-time-stamp-start <开始毫秒时间戳> \
  --message-send-time-stamp-end <结束毫秒时间戳>
```

### 2.4 时间处理

- `search:message` 使用**毫秒级 Unix 时间戳**。
- 先用 `hi calendar:get-timezone` 确认当前用户时区；默认不要假设时区。
- 相对时间（今天、昨日、近 7 天）要换算成清晰的绝对时间边界，并在汇总中标注日期。
- 对长结果分页：若返回 `hasMore=true`，只在用户目标确有需要时继续翻页；不要为了"全量"盲目翻页。

### 2.5 私聊的正确表述

- 搜索结果里可能包含私聊、群聊、机器人会话等；`search:chat` 主要是群聊搜索。
- 不能从搜索结果可靠判断"所有私聊"或"全部未读私聊"。
- 只能表述为：**"在当前检索条件下命中的相关会话/消息"**。

## 3. 发送消息（受控写操作）

### 3.1 通用规则

1. 每次调用前先运行 `hi openapi:api-detail --alias <apiAlias>`，以实时 `bizParams` 为准，禁止猜测字段或消息类型。
2. 私聊先用 `hi search:employee --query "<姓名/邮箱/薯名>"` 定位员工；唯一候选可直接使用其 `accountId/email` 作为 `receiverContactId`，多个候选必须让用户确认。
3. 群聊先用 `hi search:chat --query "<群名>"` 定位；多个候选必须让用户确认，使用结果中的真实 `chatId`。
4. 纯文本优先使用 `im:message.sendMessageToPersonal:v1` 或 `im:message.sendMessageToChat:v1`；Markdown 只有用户明确需要格式时才使用对应 Markdown 接口。
5. 需要 `operateCode` 的接口，发送前调用 `hi utils:generate-operate-code --help` 核对参数并生成新值。一次业务发送只使用一个 operateCode；状态不明时不得生成新值盲目重试，以免重复发送。
6. 文本消息通常使用 `type=1`，但仍必须以实时接口文档或已验证接口契约为准。`data` 按接口要求传字符串；若其消息协议要求 JSON 字符串，则正确转义后传入，禁止把 shell 拼接当 JSON。
7. 仅当响应 `success=true`（必要时同时有 `result.id/chatId`）才报告发送成功；否则报告失败或状态未知，不得声称已发送。
8. 禁止批量骚扰、冒充他人、发送凭证/密钥或未经用户授权的敏感内容；批量发送必须明确给出全部接收范围并再次确认。

### 3.2 私聊发送流程

```bash
hi search:employee --query "<姓名或邮箱>" --page-size 10
hi openapi:api-detail --alias "im:message.sendMessageToPersonal:v1"
hi utils:generate-operate-code --help
# 根据实时 schema 生成 operateCode 后：
hi openapi:call-api \
  --alias "im:message.sendMessageToPersonal:v1" \
  --params '<按实时 schema 构造的 JSON>'
```

推荐参数语义：`receiverContactId` 为已确认员工 contactId，`type` 为已核实的文本类型，`data` 为用户确认的正文，`abbrevContent` 为不扩大原意的简短预览，`operateCode` 为本次写操作的新幂等码。

### 3.3 群聊发送流程

```bash
hi search:chat --query "<群名称>" --page 1 --page-size 20
hi openapi:api-detail --alias "im:message.sendMessageToChat:v1"
hi utils:generate-operate-code --help
hi openapi:call-api \
  --alias "im:message.sendMessageToChat:v1" \
  --params '<按实时 schema 构造的 JSON>'
```

仅当用户明确要求时才 `@` 成员或 `@all`；`mentionContactIdList` 必须来自真实查询结果。

### 3.4 写操作失败处理

- API 明确返回失败：报告 code/msg，不自动改收件人或正文。
- 超时、断网或响应无法判断：状态记为未知；优先用近期消息检索确认是否已经出现同一正文，再由用户决定是否重试。
- 收件人搜索无结果或多义：停止发送并向用户请求确认。

## 4. 常见请求与执行路由

| 用户意图 | 路由 |
|---|---|
| "查我和某人的 hi 私聊最新进展" | 搜人员 → 限定近 7 天与对方邮箱 → `search:message` → 摘要 |
| "找某项目的群和最新消息" | `search:chat` 找群 → 按 `chatId` + 时间窗搜索消息 → 摘要 |
| "找系统/机器人提醒（审批、@等）" | `search:message` 按近期时间窗 + 关键词检索 → 按优先级去重汇总 |
| "读取/修改 hi 文档" | **不使用本 skill，切换到 `hi全家桶/hi文档操作/hi-docs`** |
| "查日程/约会议/通知汇总" | **切换到 `hi全家桶/hi其他操作`** |

## 5. 禁止事项

- 禁止任何 `hi docs:*`、`hi search:doc`、`hi search:file` 或 REDoc 文档链接读取。
- 除本 skill 明确允许且已获授权的私聊/群聊发送外，禁止拉人、改群设置、撤回消息、添加 Reaction 或执行其他 IM 写操作。
- 禁止将"搜索命中"表述为完整聊天历史、完整私聊列表、完整通知中心或真实未读状态。
- 禁止输出 token、cookie、完整附件 URL 或无关人员的敏感聊天内容。
