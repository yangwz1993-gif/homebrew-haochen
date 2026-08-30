# haochen v0.1.10 修复验收点

> 依据：双排查（产品+kimi）确认的渲染根因。给 kimi 实现（resume 同会话）。采用 kimi 方案 1+2（解析层加固 + 提示层收紧），**不动渲染器**。

## 问题回顾
- 详情答案 markdown 渲染露原形（`==/answer==`、`==高亮==`、`===标题===` 等），根因 = 模型标记输出漂移（`【answer】` 开头却 `==/answer==` 收尾 + 滥用 `==高亮==`）+ 解析层只认【】、兜底不剥 `==` + Qt setMarkdown 不渲染 `==`。

## 修复（方案 1+2）

### 1）解析层加固（conversation.py）
- `parse_paired`/`_ANSWER_PAIR`/`_SUMMARY_PAIR`：兼容模型的**错误闭合**——`==answer==`/`==/answer==`、`==summary==`/`==/summary==` 等变体视同【】系标记（正则放宽：`【answer】|==answer==` 开、`【/answer】|==/answer==` 闭），配对成功走正常路径。
- **answer 路径的兜底 `strip_tags` 补齐与 summary 一致的清洗**：剥掉残留 `==` 标记（`==/answer==`、`==/summary==` 整段标记剥掉；正文内成对 `==文字==` 保留待渲染层处理）。
- 防御：解析后正文若仍含 `==/answer==`、`==/summary==` 等整标记，一律清除。

### 2）渲染前转换（widgets.py 渲染入口，成本最低的一步）
- 在 `MarkdownView.set_markdown` 前置清洗：`==文字==` → `**文字**`（转成 Qt 支持的粗体；不做 <mark> 以免 Qt 不认）。
- `===文字===`（两头等号标题）→ `**文字**` 或 `### 文字`（转成 Qt 认的写法）。
- 纯兜底逻辑，不引入新依赖、不换渲染器。

### 3）提示层收紧（ext/index.ts ANSWER_RULES）
- 强调：闭合标记**只能用 `【/answer】`/`【/summary】`**，禁止 `==/answer==` 写法；
- `==文字==` 仅限正文内关键词高亮，少量使用，不得用于分节/闭合。

## 验收
- 重放该会话式内容（含 `==/answer==` 错误闭合、`==高亮==`、`●列表`）→ 详情页不再出现裸标记（高亮转粗体渲染、分节标记剥净）。
- 正常 `【answer】` 路径回归不受影响。
- 回归 `tests/run_all_regressions.sh --real` → PASS=7 FAIL=0；补断言：错误闭合标记被剥离、`==文字==`→粗体、`===标题===` 被转换。

## 交付
- **v0.1.10**，CHANGELOG 写条目，旧版归档；签名沿用 `662F89A7`（免重授权）。
- bump `Casks/haochen.rb`（version+sha256+url v0.1.10）push homebrew-haochen + tag；**release dmg 资产由用户网页上传**。
