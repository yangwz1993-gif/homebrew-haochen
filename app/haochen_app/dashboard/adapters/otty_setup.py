"""Bounded metadata-only Otty integration checks and explicit additive setup.

Never run an agent, mutate hook JSON/TOML, or restart existing sessions. Pi/OMP
can receive Otty's own readable template in a new file after user confirmation.
"""

from __future__ import annotations

import json
import os
import re
import stat
import tomllib
from pathlib import Path

LABELS = {"pi": "Pi", "omp": "OMP", "codex": "Codex", "claude": "Claude Code"}
TARGETS = {
    "pi": [".pi/agent/extensions/otty-integration.ts"],
    "omp": [".omp/agent/extensions/otty-integration.ts"],
    "codex": [".codex/hooks.json", ".codex/config.toml"],
    "claude": [".claude/settings.json"],
}
# codewiz-cc（CW）驱动的 Claude 会话读这个独立配置目录；Otty 官方安装器只会写
# ~/.claude/settings.json，不会写这里——CW 会话「状态未知」的常见根因。
CW_CLAUDE_CONFIG = ".cc-mirror/codewiz-cc/config/settings.json"


def agent_kind(value: object) -> str | None:
    name = str(value or "").strip().lower()
    return {"pi": "pi", "π": "pi", "omp": "omp", "oh-my-pi": "omp", "codex": "codex",
            "claude": "claude", "claude code": "claude", "claude-code": "claude"}.get(name)


def _read(path: Path, limit: int = 512_000) -> str | None:
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("集成路径包含符号链接，请在 Otty 设置中检查。")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError("集成配置无法安全检查，请在 Otty 设置中检查。")
        value = handle.read(limit + 1)
        if len(value) > limit:
            raise ValueError("集成配置过大，请在 Otty 设置中检查。")
        return value.decode("utf-8")


def _has_hook(value: object) -> bool:
    if isinstance(value, dict):
        return any(_has_hook(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_hook(item) for item in value)
    return isinstance(value, str) and ("otty-hook.sh" in value or "otty-cli" in value)


class OttyIntegrationSetup:
    def __init__(self, cli: Path | None, user_home: Path | None = None, *, socket_path: Path | None = None):
        self.cli = cli
        self.user_home = Path(user_home) if user_home is not None else Path.home()
        self.socket_path = socket_path or Path(os.environ.get(
            "OTTY_SOCKET", str(Path.home() / "Library/Application Support/io.appmakes.otty/otty.sock")))

    def inspect(self, kind: str) -> dict:
        if kind not in TARGETS:
            raise ValueError("这个 Agent 暂不支持自动检查，请在 Otty 设置中配置。")
        paths = [self.user_home / relative for relative in TARGETS[kind]]
        template = self.cli.parent.parent / "Resources/agent-integration/pi/otty-extension.ts" if self.cli else None
        result = {"kind": kind, "label": LABELS[kind], "targetPaths": [str(p) for p in paths],
                  "canInstall": False, "needsRestart": True, "integrationStatus": "unknown",
                  "nextAction": "open_otty_settings", "reasonCode": "integration_unconfirmed"}
        try:
            contents = [_read(path) for path in paths]
            if kind in {"pi", "omp"}:
                if contents[0] is None:
                    installable = bool(template and template.is_file() and self.cli and self.cli.is_file())
                    result.update(integrationStatus="missing", reasonCode="default_extension_missing",
                                  canInstall=installable,
                                  nextAction="confirm_install" if installable else "open_otty_settings",
                                  message="默认目录未找到 Otty 官方扩展；若你从其他位置加载，请先在 Otty 中确认。")
                elif "__OTTY_CLI__" in contents[0] or "__OTTY_AGENT__" in contents[0]:
                    result.update(integrationStatus="invalid", reasonCode="template_not_configured",
                                  message="扩展仍含未替换的模板变量，请在 Otty 设置中重新安装官方集成。")
                elif "OTTY_CLI" in contents[0] and "agent_start" in contents[0] and "agent_end" in contents[0]:
                    result.update(integrationStatus="present", reasonCode="restart_or_load_required",
                                  message="默认位置已有状态扩展；若未上报，请检查是否加载，并重启相应 Agent 会话。")
                else:
                    result.update(integrationStatus="conflict", reasonCode="existing_file_unrecognized",
                                  message="目标位置已有其他文件，haochen 不会覆盖；请在 Otty 设置中检查。")
            else:
                config = json.loads(contents[0]) if contents[0] is not None else {}
                has_hook = isinstance(config, dict) and _has_hook(config.get("hooks", {}))
                enabled = True
                if kind == "codex":
                    settings = tomllib.loads(contents[1]) if contents[1] is not None else {}
                    enabled = settings.get("features", {}).get("hooks") is True
                result.update(integrationStatus="present" if has_hook and enabled else "missing",
                              reasonCode="restart_or_load_required" if has_hook and enabled else
                              ("hooks_disabled" if has_hook else "official_hooks_not_found"),
                              message="已找到默认配置中的 Otty hooks；未上报时请检查信任提示并重启相应会话。"
                              if has_hook and enabled else
                              "请在 Otty 设置 → Agents 安装官方 Hooks；haochen 不改写你的现有 Agent 配置。")
            if kind == "claude":
                result.update(self._cw_claude_inspect())
        except (OSError, ValueError, UnicodeError, AttributeError):
            result.update(integrationStatus="unknown", reasonCode="config_unreadable",
                          message="暂时无法安全核对集成配置，请在 Otty 设置中检查。")
        return result

    def _cw_claude_inspect(self) -> dict:
        """CW（codewiz-cc）驱动的 Claude 会话的独立配置目录检查（只读，只回布尔，不回正文）。"""
        cw_path = self.user_home / CW_CLAUDE_CONFIG
        if not cw_path.parent.is_dir():
            return {}  # 未装 codewiz-cc：无 CW 场景
        info: dict = {"cwConfigPath": str(cw_path), "cwHooks": None}
        try:
            contents = _read(cw_path)
        except (OSError, ValueError, UnicodeError):
            info["cwNote"] = "CW 配置目录无法安全核对，请手动比对 ~/.claude/settings.json 的 Otty hooks。"
            return info
        if contents is not None:
            try:
                config = json.loads(contents)
                info["cwHooks"] = bool(isinstance(config, dict) and _has_hook(config.get("hooks", {})))
            except ValueError:
                info["cwHooks"] = None
        if info["cwHooks"] is False:
            info["cwNote"] = ("CW 驱动的 Claude 会话使用独立配置目录，其中暂无 Otty hooks，CW 会话状态会显示未知；"
                              "请在 Otty 设置安装 Claude 官方 Hooks 后，把带 _otty 标记的 hooks 组合并到该目录的 "
                              "settings.json，然后重启 CW 会话。")
        return info

    def setup(self, kind: str, *, apply: bool = False) -> dict:
        plan = self.inspect(kind)
        plan.update(applied=False, status="confirmation_required" if plan["canInstall"] else "manual_setup")
        if not apply or not plan["canInstall"]:
            return plan
        assert self.cli is not None
        source = self.cli.parent.parent / "Resources/agent-integration/pi/otty-extension.ts"
        template = _read(source, 64_000)
        if not template or not all(token in template for token in ("__OTTY_CLI__", "__OTTY_AGENT__")):
            raise ValueError("Otty 官方扩展模板不完整，未安装。")
        if not self.socket_path.is_absolute():
            raise ValueError("Otty 连接地址不是绝对路径，请在 Otty 设置中安装集成。")
        replacements = {"__OTTY_CLI__": str(self.cli.absolute()), "__OTTY_SOCKET__": str(self.socket_path),
                        "__OTTY_AGENT__": kind, "__OTTY_MARKER__": "haochen-explicit-official-template",
                        "__OTTY_VERSION__": "bundled-template"}
        for token, value in replacements.items():
            template = template.replace(token, json.dumps(value, ensure_ascii=False)[1:-1])
        if re.search(r"__OTTY_[A-Z_]+__", template):
            raise ValueError("Otty 官方扩展模板含未知变量，未安装。")
        target = self.user_home / TARGETS[kind][0]
        # Walk each path with directory descriptors: never follow a substituted
        # parent or overwrite an existing global extension, even during a race.
        absolute = target.absolute()
        fd = os.open(absolute.anchor, os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in absolute.parts[1:-1]:
                try:
                    child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                except FileNotFoundError:
                    os.mkdir(part, mode=0o700, dir_fd=fd)
                    child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
            try:
                output = os.open(absolute.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=fd)
            except FileExistsError as exc:
                raise ValueError("安装前目标文件已经出现，未覆盖，请重新检查。") from exc
            try:
                with os.fdopen(output, "w") as handle:
                    handle.write(template)
                    handle.flush()
                    os.fsync(handle.fileno())
            except BaseException:
                os.unlink(absolute.name, dir_fd=fd)
                raise
        finally:
            os.close(fd)
        return {**self.inspect(kind), "applied": True, "status": "restart_required",
                "message": "已添加 Otty 官方状态扩展。没有重启任何会话；请在方便时重启相应 Agent，再检查状态。"}
