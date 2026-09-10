#!/usr/bin/env python3
import argparse
import json
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, default=18432)
parser.add_argument("--log", required=True)
args = parser.parse_args()


def write_log(record):
    record["at"] = datetime.now(timezone.utc).isoformat()
    with open(args.log, "a", encoding="utf-8") as fp:
        fp.write(json.dumps(record, ensure_ascii=False) + "\n")


def last_user_text(payload):
    messages = payload.get("messages") or payload.get("input") or []
    for item in reversed(messages if isinstance(messages, list) else []):
        if not isinstance(item, dict) or item.get("role") != "user":
            continue
        content = item.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            texts = []
            for part in content:
                if isinstance(part, dict):
                    texts.append(str(part.get("text") or part.get("input_text") or ""))
            return " ".join(texts)
    return ""


def fixture_answer(prompt, model):
    if "长" in prompt or "详细" in prompt:
        return (
            f"已由本地自定义模型 {model} 回答。\n\n"
            "成年人的可靠感，不靠冷硬，而来自清楚、克制和兑现承诺。"
            "这段文字用于检查较长中文在结果卡片中的换行、层级、留白与可读性。"
            "第一，结论应该先出现；第二，细节应按需展开；第三，操作按钮必须始终容易辨认。"
            "当内容超过一屏时，滚动区域要稳定，文字不应贴边，底部也不该留下突兀的大块空白。"
        )
    return f"已命中本地自定义模型 {model}。我是 haochen，会用清楚、温暖的方式帮你完成桌面上的事。"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *values):
        return

    def _send_json(self, status, payload):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        write_log({"method": "GET", "path": self.path})
        if self.path.rstrip("/").endswith("models"):
            self._send_json(200, {"object": "list", "data": [{"id": "haochen-r10-local", "object": "model", "owned_by": "local-fixture"}]})
        else:
            self._send_json(200, {"ok": True})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw or b"{}")
        except Exception:
            payload = {}
        model = payload.get("model") or "missing-model"
        prompt = last_user_text(payload)
        write_log({"method": "POST", "path": self.path, "model": model, "authorization": self.headers.get("Authorization"), "prompt": prompt, "payload": payload})
        answer = fixture_answer(prompt, model)
        if self.path.rstrip("/").endswith("responses"):
            event = {
                "id": "resp_haochen_r10",
                "object": "response",
                "created_at": int(time.time()),
                "status": "completed",
                "model": model,
                "output": [{"id": "msg_haochen_r10", "type": "message", "status": "completed", "role": "assistant", "content": [{"type": "output_text", "text": answer, "annotations": []}]}],
                "output_text": answer,
                "usage": {"input_tokens": 12, "output_tokens": 32, "total_tokens": 44},
            }
            self._send_json(200, event)
            write_log({"event": "response_done", "path": self.path, "model": model, "prompt": prompt})
            return
        if not payload.get("stream"):
            self._send_json(200, {
                "id": "chatcmpl-haochen-r10", "object": "chat.completion", "created": int(time.time()),
                "model": model, "choices": [{"index": 0, "message": {"role": "assistant", "content": answer}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 32, "total_tokens": 44},
            })
            write_log({"event": "response_done", "path": self.path, "model": model, "prompt": prompt})
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        opening = {"id": "chatcmpl-haochen-r10", "object": "chat.completion.chunk", "created": int(time.time()), "model": model, "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}]}
        self.wfile.write(("data: " + json.dumps(opening, ensure_ascii=False) + "\n\n").encode("utf-8"))
        self.wfile.flush()
        for chunk in [answer[i:i + 7] for i in range(0, len(answer), 7)]:
            item = {"id": "chatcmpl-haochen-r10", "object": "chat.completion.chunk", "created": int(time.time()), "model": model, "choices": [{"index": 0, "delta": {"content": chunk}, "finish_reason": None}]}
            self.wfile.write(("data: " + json.dumps(item, ensure_ascii=False) + "\n\n").encode("utf-8"))
            self.wfile.flush()
            time.sleep(0.06)
        closing = {"id": "chatcmpl-haochen-r10", "object": "chat.completion.chunk", "created": int(time.time()), "model": model, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
        self.wfile.write(("data: " + json.dumps(closing) + "\n\ndata: [DONE]\n\n").encode("utf-8"))
        self.wfile.flush()
        write_log({"event": "response_done", "path": self.path, "model": model, "prompt": prompt})


write_log({"event": "fixture_started", "port": args.port})
ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
