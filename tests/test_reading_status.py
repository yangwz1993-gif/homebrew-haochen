import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from haochen_app.reading_status import LABELS, ReadingStatus, event_phase


def test_no_success_until_completed_content_result(qtbot):
    widget = ReadingStatus()
    qtbot.addWidget(widget)
    widget.show()
    for phase in ("binding", "bound", "capturing", "snapshot"):
        widget.set_phase(phase)
        assert "已读取" not in widget.label.text()
    assert "可以切换" in widget.label.text() and "原图" in widget.label.text()
    widget.set_phase("complete")
    assert "已读取" in widget.label.text() and not widget.scan.isVisible()
    widget.set_phase("failed")
    assert "未读到" in widget.label.text() and "已读取" not in widget.label.text()


def test_raw_snapshot_event_is_not_completion():
    assert event_phase({"type": "tool_execution_update", "toolName": "read_screen",
                        "partialResult": {"details": {"readPhase": "snapshot"}}}) == "snapshot"
    event = {"type": "tool_execution_end", "toolName": "read_screen", "result": {"details": {"readSuccess": True}}}
    assert event_phase(event) == "complete"
    event["isError"] = True
    assert event_phase(event) == "failed"
    assert "capturing" in LABELS
