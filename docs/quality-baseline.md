# v0.2.0 质量基线

记录目标：在修改业务逻辑前冻结 0.1.10 迁移基线，并明确现有检查能力与缺口。

## 已验证的旧工程基线

- 44 个 Python 文件可通过 AST 解析；
- settings selfcheck：26/26；
- mock RPC：45/45；
- Markdown：11/11；视觉扩展：21/21；P4 集成：25/25；
- 0.1.10 App 的现有代码签名结构校验通过，但 `spctl` 拒绝，且未公证；
- 现有测试以 shell/场景脚本为主，无 pytest 测试函数、覆盖率、ruff、类型检查、secret scan 或 CI 门禁；
- 已确认缺陷见 `docs/v0.2.0-development-plan.md`，本记录不把“旧测试通过”解释为“无 bug”。

## 新仓库初始环境

- Python：固定 3.12.13（`.python-version`）；
- Node：pi-source 要求 >=22.19，基线机器为 22.19.0；
- Bun：基线机器为 1.4.0；
- Python 运行依赖依据旧工程有效环境和源码 imports 迁移；
- pi-source 使用已提交的 `package-lock.json` 与 `npm ci --ignore-scripts`。

## 新仓库基线实测

- `make check`：通过（版本、secret、ruff、受控范围 pyright、shellcheck、pytest）；
- pytest 基础设施用例：4/4；覆盖率报告已生成，业务测试尚未迁入 pytest，因此初始整体覆盖率为 1%；
- mock RPC：45/45；settings：26/26；Markdown：11/11；视觉扩展：21/21；P4 集成脚本退出码 0；
- pi-source 锁文件安装成功；`check:pinned-deps` 与 `check:ts-imports` 通过；
- npm 在 Node 22.19.0 下对非引擎必需的 Gondolin workspace 给出 `>=23.6.0` 警告，但安装成功；引擎及 pi 核心声明 `>=22.19.0`。

## 新增门禁

统一入口为 `make check`，依次检查工具链版本、版本一致性、secret、ruff、pyright、shellcheck、pytest 与覆盖率报告。PyQt/PyObjC 的动态 API 暂不做全量静态类型门禁；当前先检查确定性的基础设施模块，后续重构的核心模块必须逐步加入 allowlist。业务覆盖率将在 P0/P1 修复时随行为测试提升，正式发布门槛仍为 P0 模块 ≥90%、整体 ≥80%。
