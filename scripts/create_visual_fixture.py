"""Create isolated synthetic history for native testing of the frozen application.

No credentials, network, or real user data. Prints the temporary HAOCHEN_HOME.
Launch the built app with that home and skip onboarding/permission guides.
"""

import json
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path


def main():
    home = Path(tempfile.mkdtemp(prefix="haochen-visual-rc3-"))
    sessions = home / "pi-sessions"
    sessions.mkdir(mode=0o700)
    path = sessions / "visual-fixture.jsonl"
    timestamp = datetime.now(UTC).isoformat()
    records = [{"type": "session", "version": 3, "id": str(uuid.uuid4()),
                "timestamp": timestamp, "cwd": str(home)}]
    parent = None

    def entry(kind, **payload):
        nonlocal parent
        identifier = uuid.uuid4().hex[:8]
        records.append({"type": kind, "id": identifier, "parentId": parent,
                        "timestamp": timestamp, **payload})
        parent = identifier

    entry("session_info", name="上海半日散步 · 排版测试")
    for index in range(8):
        question = f"第 {index + 1} 个问题：如何安排周末？"
        entry("message", message={"role": "user", "timestamp": 1789110000000,
                                  "content": [{"type": "text", "text": question}]})
        entry("message", message={"role": "assistant", "api": "openai-completions", "provider": "deepseek",
                                  "model": "deepseek-chat", "stopReason": "stop", "timestamp": 1789110001000,
                                  "content": [{"type": "text", "text":
            "【brief】建议去徐汇滨江：路线轻松、视野开阔，留半天就够。【/brief】"
            "【detail】## 可以这样安排\n\n"
            "先沿江散步，再找一家咖啡馆休息。下午出发，避开中午的太阳。\n\n"
            "- **路线**：龙美术馆附近出发，沿滨江慢慢走。\n"
            "- **节奏**：走走停停，不必赶着打卡。\n"
            "- **备选**：如果下雨，就换成室内展览。\n\n"
            "这是排版测试内容，不代表实时行程建议。【/detail】"}]})
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n")
    path.chmod(0o600)
    state = home / "runtime-state.json"
    state.write_text(json.dumps({"currentSession": str(path), "queue": []}))
    state.chmod(0o600)
    print(home)


if __name__ == "__main__":
    main()
