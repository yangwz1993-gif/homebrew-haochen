# haochen v0.2 安全边界

## 本地数据

- `HAOCHEN_HOME` 及其子目录必须为 `0700`；敏感文件必须为 `0600`。
- 文本文件通过同目录临时文件、`fsync`、`os.replace` 原子更新。
- 引擎以 `0077` umask 启动，确保新会话和运行文件默认不向 group/other 开放。
- 用户提问、API Key、屏幕正文不得写入诊断日志。
- 看图意图只保存 `{ "visual": true|false }`，不保存用户原始提问；升级后首次提问会删除旧 `last-user-text.txt`。
- API Key 只存于 macOS Keychain；`auth.json` 仅保留 `$HAOCHEN_<PROVIDER>_API_KEY` 引用，引擎启动时经环境变量注入。

## 远程图片

读屏模块只下载满足全部条件的 URL：

1. scheme 为 HTTPS，端口为 443，无 URL 凭据和 fragment；
2. 主机名不是 localhost/`.local`，DNS 返回的每一个 IPv4/IPv6 地址均为公网地址；
3. 每一次重定向都重新执行同样校验；
4. MIME 仅允许 PNG/JPEG/GIF/WebP，声明长度和实读长度均不超过 4 MiB；
5. 图片必须由 Pillow 完整解码，响应 MIME 必须与实际内容一致。

任何校验失败均丢弃该图片，不把错误页面、本地文件或内网响应发送给模型。

## 发布凭据

仓库不保存私钥、P12、Keychain 口令或公证凭据。正式签名仅可由 macOS Keychain 或 CI Secret 注入；旧工程暴露的签名身份必须由用户轮换/吊销。
