"""haochen 配置读写层（M-D 配置前端的核心输入，schema 以 config/README.md 为唯一依据）。

管三个文件（都在 ``haochen_home()/agent/`` 下）：

- ``models.json``   provider / 模型定义（只读展示）
- ``settings.json`` defaultProvider / defaultModel / defaultThinkingLevel / theme
- ``auth.json``     各 provider 的 API key

职责：
- 文件缺失 → 用 ``config/`` 模板初始化（ensure_initialized）
- JSON 损坏 → 抛 :class:`ConfigCorruptError`，UI 层给「重置为默认」入口
- 所有修改原子写盘（先写 tmp 再 replace），重启 App 后保留

生效语义（写盘即完成配置侧工作；引擎侧动作由 P4 接线，见 EFFECT_* 常量）：

- 同 provider 换默认模型 → 立即生效（P4 用 RPC ``set_model`` 热切换）
- 改默认 provider / 改 key → 重启引擎后生效（模型目录/凭证启动时加载）
- 默认思考档 / 主题 → 立即生效（对新会话生效）
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from haochen_app import paths
from haochen_app.engine_client import haochen_home
from haochen_app.keychain import CredentialStore, KeychainStore, credential_env_name
from haochen_app.secure_storage import atomic_write_private, ensure_private_directory, ensure_private_file

TEMPLATE_DIR = paths.config_templates()

MODELS_FILE = "models.json"
SETTINGS_FILE = "settings.json"
AUTH_FILE = "auth.json"
AUTH_TEMPLATE = "auth.json.template"  # config/ 下 auth 的模板文件名不同

# 生效语义（写入时返回给 UI 展示；P4 据此决定是否调 set_model / 提示重启）
EFFECT_IMMEDIATE = "immediate"        # 立即生效（引擎 set_model 热切换，P4 接线）
EFFECT_RESTART = "restart"            # 重启引擎后生效

EFFECT_LABEL = {
    EFFECT_IMMEDIATE: "立即生效",
    EFFECT_RESTART: "重启引擎后生效",
}

# pi 默认思考档全集（config/README.md §5："off".."max"）
THINKING_LEVELS = ["off", "minimal", "low", "medium", "high", "xhigh", "max"]

# 模板里的占位 key 视为「未配置」
_PLACEHOLDER_KEY = "sk-在此填入你的-DeepSeek-Key"


class ConfigCorruptError(Exception):
    """配置文件 JSON 损坏。``path`` 指给出问题的文件，``reset()`` 可恢复。"""

    def __init__(self, path: Path, detail: str):
        super().__init__(f"配置文件损坏: {path} ({detail})")
        self.path = path
        self.detail = detail


@dataclass
class ProviderInfo:
    """models.json 中一个 provider 的展示用视图。"""

    id: str
    name: str          # 显示名（缺省用 id）
    builtin: bool      # 无 baseUrl/api 定义 = 内建 provider
    models: list[dict] # [{id, name, reasoning, input, ...}]


class ConfigStore:
    """三个配置文件的读写门面。所有写操作立即落盘（原子替换）。"""

    def __init__(self, home: Path | None = None, keychain: CredentialStore | None = None):
        self.home = Path(home) if home else haochen_home()
        self.agent_dir = self.home / "agent"
        self.keychain = keychain or KeychainStore()

    # ── 初始化 / 重置 ─────────────────────────────────────────

    def ensure_initialized(self) -> list[Path]:
        """agent/ 或任一配置文件缺失时，用 config/ 模板补齐。返回新建的文件列表。"""
        created: list[Path] = []
        ensure_private_directory(self.home)
        ensure_private_directory(self.agent_dir)
        for name, template in (
            (MODELS_FILE, TEMPLATE_DIR / MODELS_FILE),
            (SETTINGS_FILE, TEMPLATE_DIR / SETTINGS_FILE),
            (AUTH_FILE, TEMPLATE_DIR / AUTH_TEMPLATE),
        ):
            target = self.agent_dir / name
            if not target.exists():
                atomic_write_private(target, template.read_text(encoding="utf-8"))
                created.append(target)
            else:
                ensure_private_file(target)
        self._migrate_plaintext_keys()
        return created

    def reset_to_default(self, name: str) -> Path:
        """把某个配置文件重置为模板默认（损坏恢复入口）。name 为文件名常量。"""
        template = TEMPLATE_DIR / (AUTH_TEMPLATE if name == AUTH_FILE else name)
        target = self.agent_dir / name
        ensure_private_directory(self.home)
        ensure_private_directory(self.agent_dir)
        return atomic_write_private(target, template.read_text(encoding="utf-8"))

    def _migrate_plaintext_keys(self) -> None:
        """Move legacy plaintext keys into Keychain, changing disk only after read-back succeeds."""
        path = self.agent_dir / AUTH_FILE
        if not path.exists():
            return
        try:
            auth = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return
        changed = False
        migrated: list[str] = []
        for provider, entry in auth.items() if isinstance(auth, dict) else ():
            if not isinstance(provider, str) or not isinstance(entry, dict):
                continue
            secret = entry.get("key")
            if not isinstance(secret, str) or not secret or secret == _PLACEHOLDER_KEY:
                continue
            if is_indirect_reference(secret):
                continue
            try:
                self.keychain.set(provider, secret)
                if self.keychain.get(provider) != secret:
                    raise RuntimeError("Keychain read-back verification failed")
            except Exception as exc:  # noqa: BLE001
                # 迁移失败绝不阻断启动：保留明文条目，下次启动重试。
                logging.getLogger("haochen.config").warning(
                    "keychain migration deferred for %s: %s", provider, exc)
                continue
            entry["key"] = f"${credential_env_name(provider)}"
            changed = True
            migrated.append(provider)
        if changed:
            self._save(AUTH_FILE, auth)
            logging.getLogger("haochen.config").info(
                "migrated plaintext keys to keychain: %s", migrated)

    # ── 底层读写 ──────────────────────────────────────────────

    def _load(self, name: str) -> dict:
        path = self.agent_dir / name
        if not path.exists():
            self.ensure_initialized()
        try:
            ensure_private_file(path)
            with path.open(encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            raise ConfigCorruptError(path, str(e)) from e
        if not isinstance(data, dict):
            raise ConfigCorruptError(path, "顶层不是 JSON 对象")
        return data

    def _save(self, name: str, data: dict) -> Path:
        """原子写：同目录 0600 临时文件 + replace，避免半截 JSON。"""
        payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        return atomic_write_private(self.agent_dir / name, payload)

    # ── models.json（只读展示）────────────────────────────────

    def providers(self) -> list[ProviderInfo]:
        """models.json 里的 provider 列表（含模型），供 UI 展示。"""
        data = self._load(MODELS_FILE)
        out: list[ProviderInfo] = []
        for pid, p in data.get("providers", {}).items():
            if not isinstance(p, dict):
                continue
            out.append(ProviderInfo(
                id=pid,
                name=p.get("name") or pid,
                builtin=not (p.get("baseUrl") or p.get("api")),
                models=[m for m in p.get("models", []) if isinstance(m, dict) and m.get("id")],
            ))
        return out

    # ── settings.json ─────────────────────────────────────────

    def settings(self) -> dict:
        return self._load(SETTINGS_FILE)

    def default_model(self) -> tuple[str, str]:
        """当前默认 (provider, modelId)；缺省回退到模板语义。"""
        s = self._load(SETTINGS_FILE)
        return s.get("defaultProvider", ""), s.get("defaultModel", "")

    def set_default_model(self, provider: str, model_id: str) -> str:
        """写 defaultProvider/defaultModel。返回生效语义 EFFECT_*。

        同 provider 换模型 → EFFECT_IMMEDIATE（P4 set_model 热切换）；
        换 provider        → EFFECT_RESTART（模型目录启动时加载）。
        """
        s = self._load(SETTINGS_FILE)
        old_provider = s.get("defaultProvider", "")
        s["defaultProvider"] = provider
        s["defaultModel"] = model_id
        self._save(SETTINGS_FILE, s)
        return EFFECT_IMMEDIATE if provider == old_provider else EFFECT_RESTART

    def thinking_level(self) -> str:
        return self._load(SETTINGS_FILE).get("defaultThinkingLevel", "high")

    def set_thinking_level(self, level: str) -> str:
        if level not in THINKING_LEVELS:
            raise ValueError(f"未知思考档: {level}")
        s = self._load(SETTINGS_FILE)
        s["defaultThinkingLevel"] = level
        self._save(SETTINGS_FILE, s)
        return EFFECT_IMMEDIATE

    def theme(self) -> str:
        return self._load(SETTINGS_FILE).get("theme", "dark")

    def set_theme(self, theme: str) -> str:
        s = self._load(SETTINGS_FILE)
        s["theme"] = theme
        self._save(SETTINGS_FILE, s)
        return EFFECT_IMMEDIATE

    # ── auth.json ─────────────────────────────────────────────

    def get_key(self, provider: str) -> str | None:
        """provider 的 key；未配置 / 模板占位符返回 None。

        间接引用（``$ENV_VAR`` / ``!command``）原样返回，UI 负责提示不动它。
        """
        entry = self._load(AUTH_FILE).get(provider)
        if not isinstance(entry, dict):
            return None
        key = entry.get("key")
        if not key or key == _PLACEHOLDER_KEY:
            return None
        return key

    def key_status(self, provider: str) -> tuple[bool, str]:
        """(是否已配置, 状态描述)。间接引用单独标注。"""
        key = self.get_key(provider)
        if key is None:
            return False, "未配置"
        expected = f"${credential_env_name(provider)}"
        if key == expected:
            return (True, "已安全存储在 Keychain") if self.keychain.get(provider) else (False, "Keychain 中缺少凭据")
        if key.startswith("$"):
            return True, f"已配置（环境变量 {key}）"
        if key.startswith("!"):
            return True, "已配置（命令间接引用）"
        return True, "已配置"

    def set_key(self, provider: str, key: str) -> str:
        """Store a pre-validated key in Keychain and persist only an environment reference."""
        key = key.strip()
        auth = self._load(AUTH_FILE)
        if key and is_indirect_reference(key):
            self.keychain.delete(provider)
            auth[provider] = {"type": "api_key", "key": key}
        elif key:
            self.keychain.set(provider, key)
            if self.keychain.get(provider) != key:
                raise RuntimeError("Keychain read-back verification failed")
            auth[provider] = {"type": "api_key", "key": f"${credential_env_name(provider)}"}
        else:
            self.keychain.delete(provider)
            auth.pop(provider, None)
        self._save(AUTH_FILE, auth)
        return EFFECT_RESTART


def is_indirect_reference(key: str) -> bool:
    """key 是否为 pi 原生间接引用（$ENV_VAR / !command），UI 只展示不覆盖。"""
    return key.startswith("$") or key.startswith("!")
