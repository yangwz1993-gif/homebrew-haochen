---
name: hi-docs
version: 1.2.0
description: hi 官方 REDoc 文档 Skill。当用户需要处理 https://docs.xiaohongshu.com/doc 文档链接，或明确要在 REDoc 上进行 创建/编辑/修改/查看/回复/评论/查询协作者 等文档操作时，必须使用此 skill。支持 XML/Markdown 全量内容与块级编辑、评论互动、历史与版本管理、批量文档、协作者与权限查询 等操作。不触发：带"企微/企微文档/企业微信文档”的企微文档或表格生成意向请求，请切换到 `wecomcli-doc` skill 使用。
metadata: { 'openclaw': { 'requires': { 'bins': ["node", "hi", "python3"] } } }
---

技能核心能力通过 CLI 提供，在执行 CLI 命令时，必须查看运行命令的参数，**禁止**猜测命令用法：

```bash
# 查看具体命令的参数、示例和输出格式
hi docs --help
```

# 环境准备

若执行 `hi` 命令时提示 `command not found`，通过以下任一方式全局安装：

```bash
# 方式一：npm
npm install -g @xhs/hi-cli --registry=http://npm.devops.xiaohongshu.com:7001

# 方式二：bun
bun install -g @xhs/hi-cli --registry=http://npm.devops.xiaohongshu.com:7001
```

# 文档 shortcutId

Hi 文档的 shortcutId 是文档的唯一标识符。

# 文档 spaceId

Hi 文档的 spaceId 是文档空间的唯一标识符。

# 格式选择建议

XML 和 Markdown 都可用于文档操作。建议优先使用 XML。

- XML 读取结果包含更多 Markdown 输出不支持或难以完整表达的细节，例如表格合并与列宽、单元格背景色和对齐方式、分栏比例、高亮块配置，以及标签、mention 和资源组件的属性等等。
- 使用 XML 阅读时，执行 `hi docs:get --format xml ...`。
- 使用 XML 编辑时，先通过 `docs:get --format xml` 获取最新 `hash` 和 sourcemap，再根据场景使用 `docs:edit --format xml ...` 或查找替换。
- 如果用户明确要求使用 Markdown，或 XML 无法表达目标内容、解析或编辑失败，可以改用 Markdown。

# XML 语法

**使用 XML 创建、编辑复杂结构，或需要理解组件属性时，必须先查看 REDoc XML 语法规范：**

```bash
hi docs:xml-syntax
```

以下内容通常包含较多结构和属性，建议查看 XML 语法规范后再操作：

- 表格（table）
- 高亮块（highlight）
- 分栏（columns）
- 标签（tag）
- 小红书笔记链接（red-note）
- @提及 / 艾特用户（mention）：需要获取被提及用户的邮箱等信息时，可通过 hi-search 查询
- 评论（comment）

**使用 XML 创建、插入或重写表格时，除非用户明确要求关闭标题行，否则必须在 `<table>` 上显式设置 `header-row="true"`，不要依赖省略属性后的默认行为，保证表格的美观。**

**内容中含有 `<`、`>`、`&`、`"`、`'` 等特殊符号时，必须按 XML 转义规则处理，否则会导致解析报错或渲染异常。**

# 查询文档协作者

用户问「这个文档有多少协作者 / 谁能看到这篇文档 / 谁有编辑权限」时使用本节。

**`hi docs` CLI 没有协作者查询命令**，开放平台 `redoc:permission:*` 系列接口也只能查「当前用户自己的角色」，查不到完整名单。必须走 REDoc 网页端内部接口 `queryExclusiveCollaboratorInfo`，用 SSO cookie 鉴权。

## 首选：直接跑脚本

```bash
python3 scripts/query_collaborators.py <文档链接或 shortcutId>
```

链接支持 `/doc/`、`/sheet/`、`/table/` 三种形态，带 `?smParams=...` 查询串也能直接粘贴；也可以只传 32 位 shortcutId。加 `--json` 输出结构化结果便于二次处理，`--sso` 可指定非默认登录态文件。

脚本依赖 `/home/node/sso.json` 的 `cookieHeader` 提供登录态，只用标准库，无需额外安装依赖。输出包含协作者清单（姓名 / 个人或群组 / 部门 / 角色）、文档安全级别、链接免申请开关、权限继承来源。

输出示例：

```
文档: 0731模标创新运营周会
空间: 模型标注·创新运营组
安全级别: L3-私密 · 仅文档协作者可访问、可搜到
链接免申请访问: 已关闭

协作者条目共 5 条（个人 3 · 群组/部门 2）
  [个人] 濑柚(周紫薇) · 创新数据运营2小组  →  文档所有者
  [群组] 创新AI产品&运营组  →  可编辑
  [个人] 时田(杨文著) · 创新PE策略运营1小组  →  可阅读

权限继承自上级节点: 2026 模标创新运营周会（继承角色: 文档所有者）
```

## 手动调用接口（脚本不可用时的兜底）

### 第 1 步：提取 shortcutId

从链接 `https://docs.xiaohongshu.com/doc/97f29f8d8a006fb1e63b671d5bf58633` 中取路径末段的 32 位十六进制串，丢弃 `?smParams=` 等查询参数。

### 第 2 步：取登录态 cookie

```bash
COOKIE=$(python3 -c "import json;print(json.load(open('/home/node/sso.json'))['cookieHeader'])")
```

### 第 3 步：解析 spaceId

协作者接口**必须同时传 spaceId**，缺失会失败。通过文档路径链路取，找 `type=SPACE` 的那个节点：

```bash
curl -s -H "Cookie: $COOKIE" \
  "https://docs.xiaohongshu.com/docgateway/api/menu/queryShortcutPath/<shortcutId>" \
  | python3 -c "import sys,json;print([n['spaceId'] for n in json.load(sys.stdin)['data'] if n.get('type')=='SPACE'][0])"
```

### 第 4 步：查询协作者

```bash
curl -s -H "Cookie: $COOKIE" \
  "https://docs.xiaohongshu.com/docgateway/api/shortcutMember/queryExclusiveCollaboratorInfo?shortcutId=<shortcutId>&spaceId=<spaceId>"
```

### 第 5 步：解读返回字段

| 字段 | 含义 |
|------|------|
| `data.exclusiveItemList` | 协作者数组，每项含 `memberName`、`memberType`、`department`、`role.roleName` |
| `memberType` | `1`=个人，`2`=群组，`3`=部门 |
| `role.roleCode` | `owner` 所有者 / `admin` 可管理 / `edit` 可编辑 / `view` 可阅读 |
| `data.visibilityStatus` | `0`=L3-私密，`1`=L2-内部公开，`2`=L1-公开 |
| `data.shareLinkSwitchStatus` | `1`=开启链接免申请访问，`0`=关闭 |
| `data.inheritedObjectVo` | 权限继承来源节点及继承到的角色 |

## 关键陷阱

**不要用 `batchQueryRoomMembers` 判断协作者。** 页面上还会调用 `docgateway/room/batchQueryRoomMembers`（表格版路径为 `docgateway/api/spreadsheet/room/batchQueryRoomMembers`），它返回的是**当前正在协同编辑的在线成员**，`memberCount` 会随谁开着页面而变化，与协作者名单无关。实测同一文档 room 接口返回 2 人、协作者接口返回 5 条，用错接口会得出完全错误的结论。

**群组授权要单独说明。** `memberType=2` 的一条记录背后是整个群的人，「协作者条目数」不等于「实际可访问人数」。做安全暴露审计时必须把群组条目单独拎出来，提示实际暴露面远大于条目数。

**权限面板 UI 读不到名单。** 点「权限」按钮弹出的面板只有「添加协作者」搜索框，不渲染现有协作者列表，靠截图或 DOM 抓取会误判为「无协作者」。一律以接口返回为准。

**接口返回 HTML 说明登录态失效。** 如果 curl 返回 `<!DOCTYPE html>` 而非 JSON，是 cookie 过期或路径写错；注意接口前缀是 `docgateway`，不是 `api`。

# 文档体裁模板

创建 PRD、周报、会议纪要、技术方案、项目复盘、API 文档、调研报告或综合整理文档时，先阅读 [文档体裁模板](references/document-templates.md) 选择内容骨架和版式组件。

该 reference 使用 Markdown 编写，但只描述章节结构和组件语义；实际创建和编辑仍优先使用 XML，具体格式以 `docs:xml-syntax` 为准。

# Markdown 语法

使用 Markdown 创建或编辑文档前，查看 Markdown 语法规范：

```bash
hi docs:markdown-syntax
```

# 最佳实践

## 编辑模式选择

| 场景 | 推荐模式 | 理由 |
|------|---------|------|
| 修改一段已有文字 | `--format xml --ops edit` | 精准，只动目标 block |
| 在某个位置后插入新内容 | `--format xml --ops insert_after` | 精准定位，不影响其他 block |
| 在文档末尾追加新内容 | `--format xml --append` | 自动定位最后一个 block，本质是 `insert_after` 的便捷模式 |
| 删除若干 block | `--format xml --ops remove` | 批量删，一次到位 |
| 同时修改 + 删除 + 插入（复杂排版） | `--format xml --ops`（多条组合） | 一次提交，原子性更强 |
| 整节内容乱了 / 顺序全错 | `--format xml --content` 全量替换 | 比一堆 ops 可控，不会插错位置 |

**使用 `--format xml --ops` 时，所有操作使用的顶层 `blockId` 都必须从 `docs:get --format xml` 生成的 sourcemap 中获取；涉及目标锚点时，`targetBlockId` 也必须从该 sourcemap 的顶层 `blockId` 中获取。XML 编辑不推荐使用 `--target + --replace`。**

**一次编辑包含多个操作时，可以将它们组合到同一个 `--ops` 数组中批量提交。**

## 全量替换注意事项

仅 `--content` 全量替换模式只替换文档正文，不会更改文档标题。其他编辑模式如果操作到 title block，仍可能更改文档标题。

## op 注意事项

以多条 `insert_after` 为例：指向同一个 anchor `blockId` 时，后写的先插入，最终顺序与写法顺序**相反**。

想要最终文档顺序为 `anchor → A → B → C`，ops 数组须**倒序**书写：

```json
[
  {"op": "insert_after", "blockId": "anchor", "content": "C"},
  {"op": "insert_after", "blockId": "anchor", "content": "B"},
  {"op": "insert_after", "blockId": "anchor", "content": "A"}
]
```

## 创建文档命令说明

创建命令支持同时传入 `--title`（文档标题）和 `--content`（文档正文）两个独立参数。使用 XML 时传入 `--format xml`；省略 `--format` 时使用 Markdown：

- `--title` 设置的是文档的**标题**
- `--content` 设置的是文档的**正文**，格式由 `--format` 决定，不需要在正文里重复写标题
- 两者相互独立，**不要**在 `--content` 中再次重复 `--title` 的内容

## 批量删除文档替代方案

仅当用户明确要求**批量删除多个文档**时启用本规则。单篇文档删除或文档内容删除不适用本规则。

必须明确告知用户：hi CLI 不支持真正的批量删除文档，只支持将用户指定的文档批量移动到一个指定目录下。移动完成后，用户需要在 REDoc 界面打开该目录的父级地址，手动删除这个目录。

如果用户只提供了文档/目录/空间的名称、业务线索或模糊范围，可以先使用 `hi-search` 查找相关 REDoc 文档和空间，再通过文档目录查询能力按空间或目录确认需要批量处理的节点列表。不要凭名称猜测 shortcutId 或 spaceId。

执行前必须先确认：

- 需要处理的文档列表或来源范围
- 用于收纳这些文档的目标目录 `shortcutId`
- 该目标目录的地址（通常为 `https://docs.xiaohongshu.com/doc/<目标目录shortcutId>`）
- 该目标目录的父级地址，供用户在 REDoc 界面定位并手动删除目录
- 用户明确同意执行批量移动

执行后必须返回：

- 已批量移动的文档数量和失败列表（如果有）
- 批量删除目标目录地址
- 目标目录父级地址，并提示用户需要在 REDoc 界面手动删除该目录

## 流程交互规则

### 1. 编辑文档前必须征得用户授权

当用户请求对某个 `shortcutId` 文档执行**任何编辑类操作**（包括但不限于全文修改、块级编辑、查找替换、版本恢复、内容追加等会改变文档内容的命令）时，**必须先向用户明确确认是否允许编辑该文档**，得到用户明确同意后才能执行编辑命令。

- 询问内容需包含目标文档的 `shortcutId`（如能获取到标题，也一并展示），让用户明确知道将要被修改的是哪一篇文档
- 用户未明确同意前，禁止调用任何编辑类 CLI 命令

### 2. 创建文档后告知位置

创建文档后，告诉用户文档所在的空间以及相关位置。
