# haochen v0.1.9 修复验收点

> 依据：用户真机复验 v0.1.8 看图（2026-08-29 16:14 截图）。给 kimi 实现（resume v0.1.8 会话）。

## 问题：看图模式已触发，但智谱 API 400「unsupported image」
- **现象**：用户问图，haochen 走了看图模式（真的截图发模型了），但模型 API 返回重复 3 次：
  `400 {"message":".messages[4].image[0]: You have uploaded an unsupported image. Please make sure your image is valid and in the following formats: webp, png, jpeg, and gif.","type":"invalid_request_error","param":null,"code":"invalid..."}`
- **已排除**（我方实测证据，不必再查）：
  - reader 截图 PNG **完全有效**：PNG 头正确、2420×1534、417KB、sips 可读。
  - data URL 头是标准的 `data:image/png;base64`。
  - 所以不是"图片损坏/格式不对"，而是**发送结构不符合智谱要求**。
- **重点怀疑**：报错字段是 `.messages[4].image[0]`（智谱 OpenAI 兼容接口的图像字段应为 `image_url`，报错却出现 `image[0]`）→ 疑似 haochen 发的消息里图像字段/结构不是 OpenAI 规范的 `{"type":"image_url","image_url":{"url":"data:image/png;base64,..."}}` 形式（可能塞进了智谱原生 image 字段、或 image_url 里带了非标准内容/本地路径、或 content 数组结构有误）。
- 也可能：data URL 里被追加了额外参数、或 base64 串被转义/截断。

## 要做
1. 检查 ext/index.ts（和桥接层）把截图 PNG 组装进 messages 的代码，改为**严格 OpenAI 视觉规范**：
   `{"role":"user","content":[{"type":"text","text":...},{"type":"image_url","image_url":{"url":"data:image/png;base64,<纯base64>"}}]}`
   - data URL 后必须是**纯 base64**（无换行转义问题）；
   - 不用智谱原生 `image` 字段（除非明确做智谱适配）；
   - 确认该 provider（zhipu/bigmodel）走 openai-completions 视觉消息格式。
2. 加防御：发送前校验 base64 可解码、PNG 头正确；对 >4MB 的截图先等比压缩（长边 ≤2000px）再发。
3. 回归补断言：视觉消息结构符合 OpenAI 规范（image_url + data URL 纯 base64）。

## 验收
- 用户在微信图片窗口问「这男的帅么」→ haochen 正常描述图片内容，**不再出现 400 unsupported image**。
- 普通文字问答不受影响。

## 交付
- **v0.1.9**，CHANGELOG 写条目，旧版归档；签名沿用 `662F89A7`（免重授权）。
- 回归 `--real` PASS=7 FAIL=0。
- bump `Casks/haochen.rb`（version+sha256+url v0.1.9）push homebrew-haochen；**GitHub release v0.1.9 的 dmg 资产由用户网页上传**（kimi 推 tag 即可，上传没凭据）。
