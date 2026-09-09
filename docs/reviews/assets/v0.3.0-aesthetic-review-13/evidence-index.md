# Round 13 证据索引

1. `blind-observations-sealed.md`：首次按显示名误绑后的原始封存；仅保留审计，不用于候选裁决。
2. `blind-observations-sealed.md.sha256`：上述文件封存哈希。
3. `blind-observations-invalidation.md`：误绑定发现、隔离纠正及证据失效边界。
4. `candidate-blind-observations-sealed.md`：指定候选绝对路径下的盲测原始观察。
5. `candidate-blind-observations-sealed.md.sha256`：指定候选盲测封存哈希。
6. `action-results.md`：逐项真实 UI 动作—结果记录，覆盖右键视觉确认、三场景设置快捷键、`⌘1`、`⌘N`、小窗/详情/追问、中止/重开、标题、最大化/恢复、凭据遮蔽、读屏拒绝、无凭据 Retry×3、恢复设置与完整历史。

关键结论依赖动作—结果记录；CUA 截图在执行回合中人工读取，但当前工具不提供受支持的直接落盘接口，因此没有把工具限制伪装为产品截图文件。
