"""Opt-in native reader measurement against an explicitly selected QA window.

No UI control, model calls, user configuration changes or body/image logging.
Run only with the PID/window ID/expected URL of an owned test page.
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from reader.screen_target import (  # pyright: ignore[reportMissingImports]
    attribute,
    document_id,
    fingerprint,
    matching_ax_window,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--window", type=int, required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--rounds", type=int, default=1)
    parser.add_argument("--pause-before-read", action="store_true")
    args = parser.parse_args()
    window = matching_ax_window(args.pid, args.window)
    assert window is not None, "selected QA window not found"
    uri = document_id(window)
    assert uri == args.url, "QA target differs; refusing to read another page"
    target = {"pid": args.pid, "window_id": args.window, "app": "Google Chrome",
              "title": str(attribute(window, "AXTitle") or ""), "document_uri": uri,
              "fingerprint": fingerprint(window)}
    start = time.monotonic()
    worker = subprocess.Popen([args.binary, "--serve"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, text=True)
    assert worker.stdin is not None and worker.stdout is not None
    try:
        assert json.loads(worker.stdout.readline())["event"] == "ready"
        print("startup_ms", round((time.monotonic() - start) * 1000), "worker_pid", worker.pid, flush=True)
        for number in range(args.rounds):
            worker.stdin.write(json.dumps({"id": str(number), "op": "bind", "target": target}) + "\n")
            worker.stdin.flush()
            reply = json.loads(worker.stdout.readline())
            print("binding", reply, flush=True)
            assert reply["event"] == "bound", reply
            if args.pause_before_read:
                input("Bound. Switch away using UI if required; press Enter to authorize this QA capture.\n")
            worker.stdin.write(json.dumps({"id": str(number), "op": "read"}) + "\n")
            worker.stdin.flush()
            while True:
                reply = json.loads(worker.stdout.readline())
                if reply["event"] == "snapshot":
                    print("snapshot_ms", reply["elapsed_ms"], flush=True)
                    continue
                assert reply["event"] == "result", reply
                data = reply["data"]
                assert data["window_title"] == target["title"]
                print("result", json.dumps({"round": number, "elapsed_ms": reply["elapsed_ms"],
                      "stats": data["stats"], "text_chars": sum(len(b.get("text", "")) for b in data["blocks"]),
                      "originals": data["images_original"], "missing": data["images_unavailable"],
                      "screenshot": bool(data["screenshot"])}), flush=True)
                break
    finally:
        worker.stdin.close()
        try:
            worker.wait(timeout=3)
        except subprocess.TimeoutExpired:
            worker.kill()
            worker.wait()


if __name__ == "__main__":
    main()
