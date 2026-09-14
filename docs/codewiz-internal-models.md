# 接入公司内网模型（CodeWiz LLM Proxy）

haochen 已内置 `codewiz`（OpenAI 兼容端点）与 `codewiz-gemini`（Gemini 端点）两个 provider，
指向公司内网 CodeWiz LLM Proxy。模板里只写了 `$ENV` 占位（**不含任何密钥**），因此每位使用者
只需提供**自己的** key 即可连内网模型；密钥永不入仓、永不进日志。

## 每位用户需要做两件事

1. **登录 codewiz-cc（提供 SSO）**
   本机安装并登录内部的 codewiz-cc（会在 `~/.cc-mirror/codewiz-cc/session.json` 留下会话）。
   haochen 每次启动时**临时**从该会话解出 SSO Cookie 注入引擎，既不落盘也不打印，会话过期
   自动失效。**这一步不可省**——代理缺 SSO 会直接报「无登录信息」。

2. **在 haochen 里填自己的 key + 邮箱**（推荐，应用内闭环）
   首次向导「连接模型」页 → 下拉选 **「Kimi K3（内网）」** → 填**公司邮箱 + API Key** →
   点「连接模型」。haochen 会自动带上你已登录的 SSO 去校验，通过后把 key/邮箱写进
   `~/Library/Application Support/haochen/codewiz.json`（`0600`，不入仓）→「下一步」即可用。

   > 已经建过 `codewiz.json` 且已登录的用户，选到该项会直接显示「已就绪」，无需重填。

   也可手动创建该文件（等价）：

   ```json
   {
     "apiKey": "你的-CodeWiz-QST-Key",
     "email": "you@xiaohongshu.com"
   }
   ```

完成后在模型选择里就能看到「（内网）」结尾的模型，例如
`GLM 5.3 Flash（内网）`、`Gemini 3.7 Flash（内网）` 等。

## 说明

- 只填 key 还不够，**必须同时登录 codewiz-cc** 才能拿到 SSO——这是内网代理的鉴权要求，
  不是 haochen 的限制。
- 未配置 `codewiz.json` 或未登录时，内网 provider 的密钥无法解析，模型不会误连（不会出现
  报错的“僵尸模型”）；默认的外网 `deepseek` 不受影响。
- 可用模型清单以 `config/codewiz-llm-proxy-接入指南.md` 为准；本项目内置的是其中的
  `kimi-k3 / deepseek-v4-pro-0813-ali / glm-5.3 / glm-5.3-flash / dots.llm2.inst`
  与 Gemini 端点的 `gemini-3.7-flash / gemini-3.1-pro-preview`。
  代理未提供 `gemini-3.8-flash`，故未内置（避免连到不存在的模型）。
