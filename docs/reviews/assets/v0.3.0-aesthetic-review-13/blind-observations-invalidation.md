# 盲测封存完整性说明

`blind-observations-sealed.md` 已按原要求封存且不再改写，但随后通过进程路径核验发现：系统同时存在 `/Applications/haochen.app`，首次按显示名连接 CUA 时工具透明启动并绑定了该已安装版本；指定候选虽已启动，却不是当时被操作的窗口。

因此，原封存文件只作为方法异常的原始记录，不作为 Round 13 候选结论证据。误启动的 `/Applications/haochen.app` 及其 engine 已精确关闭。后续只通过候选绝对路径 `/private/tmp/haochen-round13-candidate.riAuD0/haochen.app` 连接，并另行生成 `candidate-blind-observations-sealed.md` 与 SHA-256。
