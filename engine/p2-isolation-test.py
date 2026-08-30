#!/usr/bin/env python3
"""P2 隔离验证：引擎完全脱离 ~/.pi 运行 + 运行期间 ~/.pi 零写入。

步骤：
1. 快照 ~/.pi（所有文件 path+size+mtime+sha256）
2. 在 $HAOCHEN_HOME(默认 /tmp/haochen-p2-test) 准备 agent/{auth,models,settings}.json
   （deepseek key 从 ~/.pi/agent/auth.json 【只读】复制）
3. spawn 引擎：PI_CODING_AGENT_DIR=$HAOCHEN_HOME/agent，cwd=$HAOCHEN_HOME/pi-home，
   --session-dir $HAOCHEN_HOME/pi-sessions，发一条廉价 prompt + get_state
4. 再快照 ~/.pi，diff
5. 加第二个 provider 条目到 models.json，重启引擎，get_available_models 验证可扩展

用法: python3 p2-isolation-test.py [HAOCHEN_HOME]
退出码 0 = 全过。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(HERE, "haochen-engine")
PI_DIR = os.path.expanduser("~/.pi")
CONFIG_TPL = os.path.normpath(os.path.join(HERE, "..", "config"))

PASS = True


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        PASS = False


def snapshot(root: str) -> dict:
    out = {}
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            p = os.path.join(dirpath, f)
            try:
                st = os.stat(p)
                h = hashlib.sha256(open(p, "rb").read()).hexdigest()
                out[os.path.relpath(p, root)] = (st.st_size, st.st_mtime_ns, h)
            except OSError:
                pass
    return out


def run_engine(home: str, commands: list[dict], wait_types: list[str],
               timeout: float = 120.0) -> list[dict]:
    """spawn 引擎，逐条发命令，等到指定事件类型都出现后返回全部消息。"""
    env = dict(os.environ)
    env["PI_CODING_AGENT_DIR"] = os.path.join(home, "agent")
    env["HAOCHEN_PET"] = "1"
    proc = subprocess.Popen(
        [ENGINE, "--mode", "rpc", "--no-extensions",
         "--session-dir", os.path.join(home, "pi-sessions")],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, cwd=os.path.join(home, "pi-home"), bufsize=1, env=env)
    msgs: list[dict] = []
    cond = threading.Condition()

    def reader() -> None:
        assert proc.stdout
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                m = json.loads(line)
            except json.JSONDecodeError:
                continue
            with cond:
                msgs.append(m)
                cond.notify_all()

    threading.Thread(target=reader, daemon=True).start()
    for cmd in commands:
        assert proc.stdin
        proc.stdin.write(json.dumps(cmd, ensure_ascii=False) + "\n")
        proc.stdin.flush()
    deadline = time.time() + timeout
    with cond:
        while True:
            have = {m.get("type") for m in msgs}
            if all(t in have for t in wait_types):
                break
            remain = deadline - time.time()
            if remain <= 0:
                raise TimeoutError(f"waiting {wait_types}, got {sorted(have)}")
            cond.wait(min(remain, 0.5))
    time.sleep(0.5)
    try:
        assert proc.stdin
        proc.stdin.close()
        proc.wait(timeout=5)
    except Exception:
        proc.kill()
    return msgs


def main() -> int:
    home = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else "/tmp/haochen-p2-test"

    # ── 准备隔离环境 ──
    if os.path.exists(home):
        shutil.rmtree(home)
    os.makedirs(os.path.join(home, "agent"))
    os.makedirs(os.path.join(home, "pi-sessions"))
    os.makedirs(os.path.join(home, "pi-home"))

    src_auth = json.load(open(os.path.join(PI_DIR, "agent", "auth.json")))
    check("源 auth.json 含 deepseek 条目", "deepseek" in src_auth)
    json.dump({"deepseek": src_auth["deepseek"]},
              open(os.path.join(home, "agent", "auth.json"), "w"), indent=2)
    shutil.copy(os.path.join(CONFIG_TPL, "models.json"),
                os.path.join(home, "agent", "models.json"))
    shutil.copy(os.path.join(CONFIG_TPL, "settings.json"),
                os.path.join(home, "agent", "settings.json"))
    print(f"HAOCHEN_HOME={home} 已初始化（auth 只读复制 deepseek 条目）")

    # ── 快照 + 跑一轮真实对话 ──
    before = snapshot(PI_DIR)
    msgs = run_engine(home, [
        {"id": "p1", "type": "prompt", "message": "Reply with exactly one word: pong"},
        {"id": "s1", "type": "get_state"},
    ], ["agent_end"])
    after = snapshot(PI_DIR)

    # 对话成功
    text = ""
    for m in reversed(msgs):
        if m.get("type") == "message_end" and m.get("message", {}).get("role") == "assistant":
            text = "".join(c.get("text", "") for c in m["message"].get("content", [])
                           if c.get("type") == "text")
            if text:
                break
    check("真实对话成功（DeepSeek，隔离目录 auth）", bool(text.strip()), text.strip()[:60])

    # get_state 指向隔离目录
    st = next(m for m in msgs if m.get("id") == "s1")
    sf = st.get("data", {}).get("sessionFile", "")
    check("sessionFile 落在 $HAOCHEN_HOME/pi-sessions",
          sf.startswith(os.path.join(home, "pi-sessions")), sf)
    model = st.get("data", {}).get("model", {})
    check("默认模型来自隔离 models.json",
          model.get("provider") == "deepseek", str(model.get("id")))

    # 会话文件确实写进隔离目录
    sess_files = os.listdir(os.path.join(home, "pi-sessions"))
    check("pi-sessions 下有会话文件", any(f.endswith(".jsonl") for f in sess_files),
          str(sess_files))

    # ~/.pi 零写入
    added = set(after) - set(before)
    removed = set(before) - set(after)
    changed = [k for k in before.keys() & after.keys() if before[k] != after[k]]
    check("~/.pi 零写入（无新增/删除/修改）",
          not added and not removed and not changed,
          f"added={sorted(added)} removed={sorted(removed)} changed={sorted(changed)}")

    # ── provider 可扩展性：加第二个 provider ──
    # 注意：get_available_models 只列「凭证就绪」的 provider（model-runtime.ts:
    # available = checkAuth 通过者），所以示例 provider 必须带 apiKey 占位。
    models_path = os.path.join(home, "agent", "models.json")
    models = json.load(open(models_path))
    models["providers"]["mock-co"] = {
        "name": "MockCo（示例第二 provider）",
        "baseUrl": "https://api.mock-co.example/v1",
        "api": "openai-completions",
        "apiKey": "sk-placeholder-仅验证结构不会被调用",
        "models": [{"id": "mockco-mini", "name": "MockCo Mini",
                    "input": ["text"], "contextWindow": 128000, "maxTokens": 8192}],
    }
    json.dump(models, open(models_path, "w"), ensure_ascii=False, indent=2)
    msgs2 = run_engine(home, [{"id": "m1", "type": "get_available_models"}],
                       ["response"], timeout=30)
    resp = next(m for m in msgs2 if m.get("id") == "m1")
    avail = {(mm.get("provider"), mm.get("id"))
             for mm in resp.get("data", {}).get("models", [])}
    check("第二 provider 条目生效（get_available_models 列出）",
          ("mock-co", "mockco-mini") in avail,
          f"providers={sorted({p for p, _ in avail})}")
    check("deepseek 模型仍在", ("deepseek", "deepseek-v4-flash-vision-exp") in avail)

    print("\n==== 全部通过 ====" if PASS else "\n==== 有失败 ====")
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
