"""haochen 视觉 token（docs/visual-spec.md v1.0 的 Qt 落地，禁止硬编码规范外色值）。

所有颜色/字号/圆角/描边只从这里取；别的模块 `from .theme import C, FONT, QSS_*`。
"""

from __future__ import annotations

# ── 色板（visual-spec §1）─────────────────────────────────────
C = {
    "bg": "#fdf6e3",         # color-bg 主背景（米白）
    "surface": "#fffaf0",    # color-surface 卡片/气泡浅层
    "ink": "#2b2b22",        # color-ink 主文字/深描边
    "ink_soft": "#5c5c50",   # color-ink-soft 次级文字
    "line": "#2b2b22",       # color-line 主描边
    "line_soft": "#c8c0ac",  # color-line-soft 分割线/弱描边
    "accent": "#3a7d5c",     # color-accent 成功/确认/品牌
    "accent_tint": "#e4efe8",  # accent 极浅 tint（v0.1.7：详情页用户气泡底，与小气泡一致）
    "info": "#5b7db1",       # color-info 感知状态
    "warn": "#b8860b",       # color-warn 需确认
    "danger": "#b04a3a",     # color-danger 错误
}

# ── 字体（visual-spec §3）────────────────────────────────────
FONT = {
    "family": '"PingFang SC", "SF Pro Text", sans-serif',
    "mono": '"SF Mono", Menlo, monospace',
    "title": 15,     # title 15 / 1.4
    "body": 14,      # body 14 / 1.6
    "body_sm": 12.5,  # body-sm 12.5 / 1.5
    "code": 13,      # code 13 / 1.5（等宽）
}

# ── 描边/圆角（visual-spec §2）────────────────────────────────
RADIUS_CARD = 16   # 气泡/卡片
RADIUS_BTN = 8     # 小标签/按钮
RADIUS_INPUT = 12  # 输入框
BORDER = 2         # 主描边 px

# ── 动效（visual-spec §5）─────────────────────────────────────
ANIM_FADE_MS = 180  # 对话流逐条弹出：淡入 ease-out


def px(v: float) -> str:
    """QSS 字号：12.5 → 12.5px。"""
    return f"{v}px"


# ── 全局 QSS ──────────────────────────────────────────────────
def app_stylesheet() -> str:
    return f"""
    QWidget {{
        background: {C['bg']};
        color: {C['ink']};
        font-family: {FONT['family']};
        font-size: {px(FONT['body'])};
    }}
    QToolTip {{
        background: {C['surface']}; color: {C['ink']};
        border: 1px solid {C['line_soft']};
    }}
    QScrollBar:vertical {{
        background: transparent; width: 8px; margin: 2px;
    }}
    QScrollBar::handle:vertical {{
        background: {C['line_soft']}; border-radius: 4px; min-height: 24px;
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QMenu {{
        background: {C['surface']};
        border: {BORDER}px solid {C['line']};
        border-radius: {RADIUS_BTN}px;
        padding: 4px;
    }}
    QMenu::item {{ padding: 6px 18px; border-radius: 4px; }}
    QMenu::item:selected {{ background: {C['accent']}; color: {C['bg']}; }}
    QInputDialog QLineEdit, QLineEdit {{
        background: {C['surface']};
        border: {BORDER}px solid {C['line']};
        border-radius: {RADIUS_INPUT}px;
        padding: 6px 10px;
    }}
    QDialog {{ background: {C['bg']}; }}
    /* v0.1.4 §2：原生对话框去系统灰，统一米白卡片化 */
    QMessageBox {{ background: {C['bg']}; }}
    QMessageBox QLabel {{ color: {C['ink']}; background: transparent; }}
    QMessageBox QPushButton {{
        background: {C['surface']}; color: {C['ink']};
        border: {BORDER}px solid {C['line']};
        border-radius: {RADIUS_BTN}px;
        padding: 5px 16px;
        min-width: 64px;
    }}
    QMessageBox QPushButton:hover {{ background: {C['bg']}; }}
    QMessageBox QPushButton:pressed {{ background: {C['line_soft']}; }}
    """


def button_solid() -> str:
    """主按钮（实心，color-accent）。"""
    return f"""
    QPushButton {{
        background: {C['accent']}; color: {C['bg']};
        border: {BORDER}px solid {C['line']};
        border-radius: {RADIUS_BTN}px;
        padding: 5px 16px;
        font-weight: bold;
    }}
    QPushButton:hover {{ background: {C['info']}; }}
    QPushButton:pressed {{ background: {C['ink']}; }}
    QPushButton:disabled {{ background: {C['line_soft']}; color: {C['ink_soft']}; }}
    """


def button_outline() -> str:
    """次按钮（描边）。"""
    return f"""
    QPushButton {{
        background: transparent; color: {C['ink']};
        border: {BORDER}px solid {C['line']};
        border-radius: {RADIUS_BTN}px;
        padding: 5px 16px;
    }}
    QPushButton:hover {{ background: {C['surface']}; }}
    QPushButton:pressed {{ background: {C['line_soft']}; }}
    """
