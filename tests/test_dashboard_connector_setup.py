"""Onboarding diagnostics and additive setup, only in isolated test homes."""

from __future__ import annotations

import base64
import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from haochen_app.dashboard.adapters.browser import BUNDLED_EXTENSION_ID, BrowserAdapter  # noqa: E402
from haochen_app.dashboard.adapters.otty import OttyAdapter  # noqa: E402
from haochen_app.dashboard.adapters.otty_setup import OttyIntegrationSetup, agent_kind  # noqa: E402
from haochen_app.dashboard.browser_host import BrowserStore  # noqa: E402

TEMPLATE = '''// marker: __OTTY_MARKER__
// otty-extension-version: __OTTY_VERSION__
const OTTY_CLI = "__OTTY_CLI__";
const OTTY_SOCKET = "__OTTY_SOCKET__";
const OTTY_AGENT = "__OTTY_AGENT__";
export default (pi) => { pi.on("agent_start", () => {}); pi.on("agent_end", () => {}); };
'''


@pytest.fixture
def otty_setup(tmp_path):
    cli = tmp_path / "Otty.app/Contents/MacOS/otty-cli"
    cli.parent.mkdir(parents=True)
    cli.write_text("synthetic")
    template = cli.parent.parent / "Resources/agent-integration/pi/otty-extension.ts"
    template.parent.mkdir(parents=True)
    template.write_text(TEMPLATE)
    return OttyIntegrationSetup(cli, tmp_path / "user")


@pytest.mark.parametrize("value,expected", [("pi", "pi"), ("π", "pi"), ("Codex", "codex"),
                                           ("Claude Code", "claude"), ("omp", "omp"), ("arbitrary", None)])
def test_agent_kind_is_bounded(value, expected):
    assert agent_kind(value) == expected


def test_otty_plan_never_creates_or_restarts(otty_setup):
    result = otty_setup.setup("pi")
    assert result["canInstall"] and result["status"] == "confirmation_required"
    assert result["reasonCode"] == "default_extension_missing" and not result["applied"]
    assert not otty_setup.user_home.exists()


@pytest.mark.parametrize("kind,directory", [("pi", ".pi"), ("omp", ".omp")])
def test_otty_explicit_setup_adds_only_official_extension(otty_setup, kind, directory):
    result = otty_setup.setup(kind, apply=True)
    assert result["applied"] and result["needsRestart"]
    target = otty_setup.user_home / directory / "agent/extensions/otty-integration.ts"
    content = target.read_text()
    assert "__OTTY_" not in content and f'const OTTY_AGENT = "{kind}"' in content
    assert f'const OTTY_SOCKET = "{otty_setup.socket_path}"' in content
    assert str(otty_setup.cli) in content and target.stat().st_mode & 0o777 == 0o600
    assert otty_setup.inspect(kind)["integrationStatus"] == "present"
    assert [p for p in otty_setup.user_home.rglob("*") if p.is_file()] == [target]
    before = target.stat().st_mtime_ns
    assert not otty_setup.setup(kind, apply=True)["applied"]
    assert target.stat().st_mtime_ns == before


def test_otty_never_overwrites_conflict(otty_setup):
    target = otty_setup.user_home / ".pi/agent/extensions/otty-integration.ts"
    target.parent.mkdir(parents=True)
    target.write_text("user-owned extension")
    result = otty_setup.setup("pi", apply=True)
    assert result["integrationStatus"] == "conflict" and not result["applied"]
    assert target.read_text() == "user-owned extension"


def test_otty_setup_requires_absolute_socket_and_does_not_need_agent_home_env(otty_setup):
    otty_setup.socket_path = Path("relative/socket")
    with pytest.raises(ValueError, match="绝对路径"):
        otty_setup.setup("pi", apply=True)
    assert not otty_setup.user_home.exists()


def test_otty_rejects_symlink_parent(otty_setup, tmp_path):
    destination = tmp_path / "outside"
    destination.mkdir()
    otty_setup.user_home.mkdir()
    (otty_setup.user_home / ".pi").symlink_to(destination, target_is_directory=True)
    result = otty_setup.setup("pi", apply=True)
    assert result["reasonCode"] == "config_unreadable" and not result["applied"]
    assert list(destination.iterdir()) == []


def test_otty_codex_and_claude_remain_manual(otty_setup):
    for kind in ("codex", "claude"):
        result = otty_setup.setup(kind, apply=True)
        assert result["nextAction"] == "open_otty_settings"
        assert not result["canInstall"] and not result["applied"]
    assert not otty_setup.user_home.exists()


def test_otty_hook_detection_does_not_return_config_bodies(otty_setup):
    home = otty_setup.user_home / ".codex"
    home.mkdir(parents=True)
    (home / "hooks.json").write_text(json.dumps({"hooks": {"SessionStart": [{"command": "otty-hook.sh idle"}]},
                                                "private": "never-return-this"}))
    (home / "config.toml").write_text("[features]\nhooks = false\n")
    assert otty_setup.inspect("codex")["reasonCode"] == "hooks_disabled"
    (home / "config.toml").write_text("[features]\nhooks = true\n")
    result = otty_setup.inspect("codex")
    assert result["integrationStatus"] == "present"
    assert "never-return-this" not in json.dumps(result)


def test_otty_diagnostics_distinguish_detected_agent_from_reporting(otty_setup, monkeypatch):
    adapter = OttyAdapter(otty_setup.cli, user_home=otty_setup.user_home)
    adapter._cli = otty_setup.cli
    event = {"agent": "pi", "state": "unknown", "sessionId": "", "title": "PRIVATE title"}
    monkeypatch.setattr(adapter, "snapshot", lambda: {"status": "partial", "checkedAt": "now",
                                                     "message": "safe", "events": [event]})
    result = adapter.diagnostic()
    assert result["agents"][0]["reportingCount"] == 0
    assert result["agents"][0]["canInstall"]
    assert "PRIVATE" not in json.dumps(result)
    event.update(state="processing", sessionId="session")
    result = adapter.diagnostic()
    assert result["agents"][0]["reasonCode"] == "reporting"
    assert not result["agents"][0]["canInstall"]  # Could have loaded integration from elsewhere.


def test_otty_diagnostic_does_not_consume_service_lifecycle_transition(otty_setup, monkeypatch):
    adapter = OttyAdapter(otty_setup.cli, user_home=otty_setup.user_home)
    previous = {"otty:synthetic": {"state": "processing"}}
    adapter._previous, adapter._baseline_valid, adapter._last_success = previous, True, "before"

    def changed_snapshot():
        adapter._previous = {"otty:synthetic": {"state": "idle"}}
        adapter._baseline_valid, adapter._last_success = False, "after"
        return {"status": "ready", "checkedAt": "now", "message": "safe", "events": []}

    monkeypatch.setattr(adapter, "snapshot", changed_snapshot)
    adapter.diagnostic()
    assert adapter._previous is previous and adapter._baseline_valid and adapter._last_success == "before"


@pytest.fixture
def chrome_setup(tmp_path):
    adapter = BrowserAdapter(tmp_path / "haochen")
    chrome = tmp_path / "chrome"
    command = [sys.executable, "--browser-host"]
    return adapter, chrome, command


def test_chrome_setup_check_is_genuinely_readonly(chrome_setup):
    adapter, chrome, command = chrome_setup
    result = adapter.setup_state(chrome_home=chrome, command=command)
    assert result["stage"] == "extension" and result["reasonCode"] == "bridge_missing"
    assert result["extensionInstalled"] == "unknown"  # No fake assertion based on a missing host.
    assert not adapter.home.exists() and not chrome.exists()


def test_chrome_setup_distinguishes_host_from_extension(chrome_setup):
    adapter, chrome, command = chrome_setup
    adapter.install_host(BUNDLED_EXTENSION_ID, chrome_home=chrome, command=command)
    result = adapter.setup_state(chrome_home=chrome, command=command)
    assert result["stage"] == "bridge" and result["runtimeValid"]
    assert result["reasonCode"] == "extension_not_connected"
    assert result["extensionInstalled"] == "unknown"


def test_chrome_setup_runtime_repair_and_foreign_manifest(chrome_setup, tmp_path):
    adapter, chrome, command = chrome_setup
    installed = adapter.install_host(BUNDLED_EXTENSION_ID, chrome_home=chrome, command=command)
    changed = [sys.executable, "changed-command"]
    assert adapter.setup_state(chrome_home=chrome, command=changed)["reasonCode"] == "bridge_runtime_changed"
    adapter.install_host(BUNDLED_EXTENSION_ID, chrome_home=chrome, command=changed)
    assert adapter.setup_state(chrome_home=chrome, command=changed)["runtimeValid"]
    path = Path(installed["manifestPath"])
    data = json.loads(path.read_text())
    data["path"] = str(tmp_path / "unrelated-launcher")
    path.write_text(json.dumps(data))
    before = path.read_bytes()
    with pytest.raises(ValueError, match="另一个安装位置"):
        adapter.install_host(BUNDLED_EXTENSION_ID, chrome_home=chrome, command=command)
    assert path.read_bytes() == before


def test_chrome_connection_authorization_and_capture_are_separate(chrome_setup):
    adapter, chrome, command = chrome_setup
    adapter.install_host(BUNDLED_EXTENSION_ID, chrome_home=chrome, command=command)
    store = BrowserStore(adapter.home)
    try:
        session = store.connect("synthetic-client")
        result = adapter.setup_state(chrome_home=chrome, command=command)
        assert result["stage"] == "authorization" and result["reasonCode"] == "page_not_selected"
        assert result["extensionInstalled"] == "confirmed"
        source = {"type": "observation", "sourceId": "synthetic-source", "url": "http://localhost:8765/demo",
                  "title": "Private synthetic title", "content": "Private synthetic content",
                  "status": "permission_required", "coverage": "none", "tabId": 7, "windowId": 2}
        store.observe(source, session, "synthetic-client")
        result = adapter.setup_state(chrome_home=chrome, command=command)
        assert result["reasonCode"] == "site_permission_required" and result["missingPermissionCount"] == 1
        source.update(status="available", coverage="main_frame_text")
        store.observe(source, session, "synthetic-client")
        result = adapter.setup_state(chrome_home=chrome, command=command)
        assert result["stage"] == "ready" and result["availableSourceCount"] == 1
        assert "Private" not in json.dumps(result) and "localhost" not in json.dumps(result)
        store.disconnect(session)
        assert adapter.setup_state(chrome_home=chrome, command=command)["stage"] == "bridge"
    finally:
        store.close()


def test_chrome_bundled_extension_id_is_stable_public_key():
    manifest = json.loads((ROOT / "browser-extension/manifest.json").read_text())
    digest = hashlib.sha256(base64.b64decode(manifest["key"])).hexdigest()[:32]
    assert "".join(chr(ord("a") + int(value, 16)) for value in digest) == BUNDLED_EXTENSION_ID
    assert "host_permissions" not in manifest


def test_chrome_popup_refreshes_async_connection_without_reopening():
    script = (ROOT / "browser-extension/popup.js").read_text()
    assert "setInterval" in script and "document.hidden" in script and '"pagehide"' in script
    assert 'contains(document.activeElement)' in script


def test_cw_claude_hooks_detection(otty_setup):
    """CW（codewiz-cc）驱动的 Claude 会话读独立配置目录；探测只回布尔，不回正文。"""
    result = otty_setup.inspect("claude")
    assert "cwHooks" not in result  # 未装 codewiz-cc：无 CW 场景字段

    cw = otty_setup.user_home / ".cc-mirror/codewiz-cc/config"
    cw.mkdir(parents=True)
    (cw / "settings.json").write_text(json.dumps({"hooks": {}, "secret": "never-return-this"}))
    result = otty_setup.inspect("claude")
    assert result["cwHooks"] is False and "CW" in result["cwNote"]
    assert "never-return-this" not in json.dumps(result)

    (cw / "settings.json").write_text(json.dumps({"hooks": {"SessionStart": [{"command": "otty-hook.sh idle"}]}}))
    result = otty_setup.inspect("claude")
    assert result["cwHooks"] is True and "cwNote" not in result


def test_otty_version_gate_flags_old_versions(otty_setup, monkeypatch):
    """旧版 Otty 没有 agent_state 字段：诊断必须明确提示升级，而不是静默「状态未知」。"""
    import plistlib
    adapter = OttyAdapter(otty_setup.cli, user_home=otty_setup.user_home)
    adapter._cli = otty_setup.cli
    monkeypatch.setattr(adapter, "snapshot", lambda: {"status": "ready", "checkedAt": "now",
                                                      "message": "ok", "events": []})
    plist = otty_setup.cli.parent.parent / "Info.plist"
    result = adapter.diagnostic()
    assert "upgradeRequired" not in result  # 无 Info.plist → 版本未知 → 不猜不拦

    plist.write_bytes(plistlib.dumps({"CFBundleShortVersionString": "1.3.0"}))
    result = adapter.diagnostic()
    assert result["upgradeRequired"] is True and "1.4.1" in result["message"]
    assert result["ottyVersion"] == "1.3.0"

    plist.write_bytes(plistlib.dumps({"CFBundleShortVersionString": "1.4.1"}))
    assert "upgradeRequired" not in adapter.diagnostic()


def test_distribution_doc_covers_otty_prerequisites():
    """分发文档必须包含 Otty 前置链与 CW 指引（防文档回退，措辞守卫）。"""
    doc = (ROOT / "docs" / "homebrew-install.md").read_text(encoding="utf-8")
    for needle in ("Otty", "1.4.1", "Agents", "重启", ".cc-mirror"):
        assert needle in doc, f"homebrew-install.md 缺少 Otty 前置要素：{needle}"
