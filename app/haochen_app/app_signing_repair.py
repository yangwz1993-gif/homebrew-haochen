"""P8 一键修复签名权限（方案 B 前端化）。

用户不满 ad-hoc 每次重建换 DR → TCC 授权不持久/对不上。本模块在 App 内一键：
  1) 生成**稳定**自签签名身份（openssl+security，专用钥匙串）；
  2) `security add-trusted-cert` 授信（唯一需管理员，用 osascript 弹系统密码框）；
  3) `codesign` 用稳定身份重签当前 haochen.app（DR 固定）；
  4) App 自动退出 → 后台脚本重签 → 重开（用户感知：点一下自动搞定，不用手动重启）；
  5) 重开后 `ensure_permissions` 授权一次（辅助功能+屏幕录制），此后授权持久。

加密/证书材料存 HAOCHEN_HOME/signing/（chmod 600，App 内部使用，用户无需记）。
只在检测到「当前 app 为 ad-hoc（不稳定签名）」时启用；已稳定签名时按钮隐藏/显示已修复。
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

log = logging.getLogger("haochen.onesign")

IDENTITY = "haochen Local Signing"
_KC_NAME = "haochen-signing.keychain-db"
_CERT_CN = "haochen-signing.pem"
_KEY_CN = "haochen-signing-key.pem"
_PW_FILE = "signing.keychain-pw"
_DAYS = 1095


# ── 工具 ──────────────────────────────────────────────────────

def _kc_path() -> Path:
    env = os.environ.get("HAOCHEN_SIGNING_KC")
    return Path(env) if env else Path.home() / "Library" / "Keychains" / _KC_NAME


def _add_kc_to_search() -> None:
    """把签名钥匙串加入用户搜索列表——否则 codesign --keychain 找不到身份。"""
    kc = str(_kc_path())
    r = _run(["security", "list-keychains", "-d", "user"])
    existing = [x.strip().strip('"') for x in r.stdout.splitlines() if x.strip()]
    if kc not in existing:
        _run(["security", "list-keychains", "-d", "user", "-s", kc] + existing)


def _out_dir(home) -> Path:
    home = Path(home)
    d = home / "signing"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    """运行命令，捕获输出，不抛异常。"""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, **kw)
    except Exception as exc:  # noqa: BLE001
        return subprocess.CompletedProcess(cmd, -1, "", str(exc))


def app_bundle() -> Path | None:
    """当前 app 的 bundle 根（.app，非 Contents）。frozen 为 Contents 的父。

    修复：原返回 Contents（错）→ is_stable_signed 检查错路径误报 ad-hoc。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent.parent.parent  # Contents/.. = .app 根
    return None  # 脚本形态（研发/未打包）无 bundle


def is_stable_signed() -> bool:
    """当前 app 是否已用稳定身份签名（非 adhoc）。"""
    app = app_bundle()
    if app is None:
        return False
    r = _run(["codesign", "-dvv", str(app)])
    out = r.stdout + r.stderr
    return ("Signature=adhoc" not in out) and (IDENTITY in out)


def identity_exists() -> bool:
    """稳定签名身份是否已在专用钥匙串。"""
    kc = _kc_path()
    if not kc.exists():
        return False
    r = _run(["security", "find-identity", "-p", "codesigning", "-v", str(kc)])
    return IDENTITY in (r.stdout + r.stderr)


# ── 1) 生成稳定签名身份（一次性）──────────────────────────────

def _generate_identity(home: Path) -> tuple[bool, str, str | None]:
    """生成 cert/key/身份（含 private key partition），返回 (ok, msg, cert_path)。"""
    out = _out_dir(home)
    cert = out / _CERT_CN
    key = out / _KEY_CN
    pw_file = out / _PW_FILE
    kc = _kc_path()

    # 已有身份 → 直接复用（已受信，无需再授信、无需读 pem；避免读不存在的旧 pem）
    if identity_exists():
        return True, "签名身份已存在", None

    # 生成自签证书

    # 生成自签证书
    r = _run(["openssl", "req", "-new", "-newkey", "rsa:2048", "-x509", "-nodes",
              "-days", str(_DAYS), "-subj", f"/CN={IDENTITY}",
              "-addext", "keyUsage=critical,digitalSignature",
              "-addext", "extendedKeyUsage=codeSigning",
              "-keyout", str(key), "-out", str(cert)])
    if r.returncode != 0:
        return False, f"gen cert 失败: {r.stderr.strip()}", None

    # 导出 p12（legacy 兼容 security）
    pw = os.urandom(9).hex()
    p12 = out / "haochen-signing.p12"
    r = _run(["openssl", "pkcs12", "-export", "-legacy", "-out", str(p12),
              "-inkey", str(key), "-in", str(cert), "-passout", f"pass:{pw}"])
    if r.returncode != 0:
        return False, f"pkcs12 失败: {r.stderr.strip()}", None

    # 创建/解锁专用钥匙串
    r = _run(["security", "create-keychain", "-p", pw, str(kc)])
    r = _run(["security", "unlock-keychain", "-p", pw, str(kc)])

    # 导入身份
    r = _run(["security", "import", str(p12), "-k", str(kc), "-P", pw,
              "-T", "/usr/bin/codesign", "-T", "/usr/bin/security"])
    if r.returncode != 0:
        return False, f"import 失败: {r.stderr.strip()}", None

    # 允许 codesign 免弹窗访问私钥
    _run(["security", "set-key-partition-list", "-S", "apple-tool:,apple:,codesign:",
          "-s", "-k", pw, str(kc)])
    _add_kc_to_search()   # 让 codesign 能搜到该钥匙串

    pw_file.write_text(pw, encoding="utf-8")
    os.chmod(pw_file, 0o600)
    return True, "签名身份已生成", str(cert)


# ── 2) 授信（用户域，无需 admin/密码框）──────────────────────────

def _trust_cert_user(cert: Path) -> tuple[bool, str]:
    """用户域授信：加入当前用户信任库（非 -d 系统域），供 codesign 识别稳定身份。

    修复：原用 `-d`(admin 域) + `do shell script with administrator privileges`，其
    SecTrustSettingsSetTrustSettings 需独立用户交互授权，在脚本上下文被拒（"no user
    interaction was possible"）。改用**用户域**（去 -d），属于用户自己的信任库，无需 admin、
    也不触发该交互授权 → 直接成功。
    """
    if not Path(cert).exists():
        return False, f"证书不存在: {cert}"  # 防御：正常流不会走到（identity 已存在则不授信）
    kc = _kc_path()
    r = _run(["security", "add-trusted-cert", "-r", "trustRoot", "-p", "codeSign",
              "-k", str(kc), str(cert)])
    return r.returncode == 0, (r.stdout or r.stderr or "").strip()


# ── 3/4) 重签 + 自动退出→重签→重开 ────────────────────────────

def _identity_hash() -> str | None:
    """从签名钥匙串解析身份 SHA-1（避免身份名跨钥匙串歧义）。"""
    r = _run(["security", "find-identity", "-p", "codesigning", "-v", str(_kc_path())])
    m = re.search(r"([0-9A-F]{40})", r.stdout)
    return m.group(1) if m else None


def _resign_current_app(home: Path) -> tuple[bool, str]:
    """重签当前已安装 app（DR 固定）。"""
    app = app_bundle()
    if app is None:
        return False, "非打包形态，无法重签"
    kc = _kc_path()
    pw = (_out_dir(home) / _PW_FILE).read_text().strip()
    if not pw:
        return False, "缺钥匙串密码"
    _run(["security", "unlock-keychain", "-p", pw, str(kc)])
    ident = _identity_hash() or IDENTITY
    r = _run(["codesign", "--force", "--deep", "--sign", ident,
              "--keychain", str(kc), str(app)])
    if r.returncode != 0:
        return False, f"codesign 失败: {r.stderr.strip()}"
    return True, "已重签（新 DR 稳定）"


def schedule_relaunch_and_resign(home: Path) -> tuple[bool, str]:
    """重签后：后台 detached 脚本 等 App 退出 → 重签 → 重开。返回是否调度成功。"""
    app = app_bundle()
    if app is None:
        return False, "非打包形态"
    kc = _kc_path()
    out = _out_dir(home)
    pw = (out / _PW_FILE).read_text().strip()
    ident = _identity_hash() or IDENTITY

    script = (
        "sleep 1.5; "
        f"security unlock-keychain -p '{pw}' '{kc}'; "
        f"codesign --force --deep --sign '{ident}' --keychain '{kc}' '{app}'; "
        f"open '{app}'"
    )
    # detached 新 session：App 退出后由 launchd 收养，独立跑完
    try:
        subprocess.Popen(["sh", "-c", script], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True, "已调度：自动重签并重开"
    except Exception as exc:  # noqa: BLE001
        return False, f"调度失败: {exc}"


# ── 主流程（UI 调用）────────────────────────────────────────────

def repair_signing(home: Path, parent=None) -> dict:
    """一键修复：生成身份 → 授信 → 调度重签重开。返回 {ok, stage, msg, needs_quit}。"""
    home = Path(home)
    if app_bundle() is None:
        return {"ok": False, "stage": "done", "msg": "非打包形态，无法在 App 内修复", "needs_quit": False}
    if is_stable_signed():
        return {"ok": True, "stage": "done", "msg": "已用稳定签名，无需修复", "needs_quit": False}

    # 1) 生成身份
    ok, msg, cert = _generate_identity(home)
    if not ok:
        return {"ok": False, "stage": "identity", "msg": msg, "needs_quit": False}

    # 2) 用户域授信（若已存在身份则 cert=None，已受信，无需再授信）
    if cert:
        t_ok, t_msg = _trust_cert_user(Path(cert))
        if not t_ok:
            return {"ok": False, "stage": "trust", "msg": f"授信失败（{t_msg}）", "needs_quit": False}

    # 3) 调度重签 + 重开（App 即将退出）
    _add_kc_to_search()   # 确保后台 codesign 也能搜到身份
    s_ok, s_msg = schedule_relaunch_and_resign(home)
    if not s_ok:
        # 兜底：直接重签（若允许），提示重启
        return {"ok": False, "stage": "relaunch", "msg": s_msg, "needs_quit": False}
    return {"ok": True, "stage": "relaunch", "msg": "已修复并调度自动重启，App 即将退出重开", "needs_quit": True}
