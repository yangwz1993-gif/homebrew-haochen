"""haochen 对话前端（M-C 完整聊天窗口）。

P4 集成入口：

    from haochen_app.chat import ChatWindow
    win = ChatWindow(engine_client)   # 复用壳的 EngineClient；不传则自建（HAOCHEN_MOCK=1 走 mock）
    win.start()                       # 启动引擎 + 拉会话状态
    win.show()
"""

from .window import ChatWindow

__all__ = ["ChatWindow"]
