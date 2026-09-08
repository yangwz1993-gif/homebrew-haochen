# 参与开发

感谢参与 haochen。项目优先保证桌面交互流畅、用户数据安全和 macOS 原生体验。

## 分支与提交

- `main` 始终保持可测试、可构建；不直接提交功能开发。
- 从最新 `main` 创建短生命周期分支：`feat/<topic>`、`fix/<topic>`、`docs/<topic>`、`chore/<topic>`。
- 通过 Pull Request 合并，默认使用 Squash merge。
- 提交标题采用 Conventional Commits，例如 `feat(pet): add transient result bubble`。
- 不在分支名、提交、Issue、PR、测试夹具或截图中写入凭据和私人数据。

## 开发流程

```bash
make bootstrap
make check
```

涉及交互的改动还必须运行对应场景验证，并检查截图：

```bash
HAOCHEN_MOCK=1 MOCK_TICK_MS=20 QT_QPA_PLATFORM=offscreen \
  app/.venv/bin/python app/haochen_app/pet/verification/run_scenarios.py

HAOCHEN_MOCK=1 MOCK_TICK_MS=5 QT_QPA_PLATFORM=offscreen \
  app/.venv/bin/python app/haochen_app/chat/verification/run_scenarios.py
```

不要把仅“信号到达”当成视觉验收；结果必须在最终截图中可见、可读、没有遮挡和异常留白。

## Pull Request 要求

- 说明用户问题、实现方案、风险和回滚方式。
- 列出实际执行的测试；UI 改动提供前后截图或录屏。
- 新行为有自动化回归；Bug 修复先增加能复现问题的测试。
- 更新 `CHANGELOG.md` 的 `[Unreleased]`。
- 版本、Cask、签名和发布文件只由发布流程更新。

## 代码审查重点

1. 用户数据与权限边界是否收紧而非放宽。
2. 气泡与完整窗口是否共享同一会话和状态。
3. 失败、取消、引擎崩溃和多屏切换是否闭环。
4. Reduce Motion、键盘操作和 VoiceOver 是否仍可用。
5. 是否引入主线程阻塞、无界日志、明文凭据或路径越界。
