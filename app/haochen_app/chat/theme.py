"""haochen 视觉 token（docs/visual-spec.md v1.0 的 Qt 落地，禁止硬编码规范外色值）。

所有颜色/字号/圆角/描边只从这里取；别的模块 `from .theme import C, FONT, QSS_*`。
"""

from __future__ import annotations

# ── 色板（visual-spec §1）─────────────────────────────────────
from ..appearance import BUTTON_RADIUS, CARD_RADIUS, COLORS, FONTS, INPUT_RADIUS

C = COLORS

# ── 字体（visual-spec §3）────────────────────────────────────
FONT = FONTS

# ── 描边/圆角（visual-spec §2）────────────────────────────────
RADIUS_CARD = CARD_RADIUS
RADIUS_BTN = BUTTON_RADIUS
RADIUS_INPUT = INPUT_RADIUS
BORDER = 1

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
    QLabel {{ background: transparent; }}
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
        border: {BORDER}px solid {C['line_soft']};
        border-radius: {RADIUS_BTN}px;
        padding: 4px;
    }}
    QMenu::item {{ padding: 6px 18px; border-radius: 4px; }}
    QMenu::item:selected {{ background: {C['accent']}; color: {C['bg']}; }}
    QInputDialog QLineEdit, QLineEdit {{
        background: {C['surface']};
        border: {BORDER}px solid {C['line_soft']};
        border-radius: {RADIUS_INPUT}px;
        padding: 6px 10px;
    }}
    QDialog {{ background: {C['bg']}; }}
    /* v0.1.4 §2：原生对话框去系统灰，统一米白卡片化 */
    QMessageBox {{ background: {C['bg']}; }}
    QMessageBox QLabel {{ color: {C['ink']}; background: transparent; }}
    QMessageBox QPushButton {{
        background: {C['surface']}; color: {C['ink']};
        border: {BORDER}px solid {C['line_soft']};
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
        border: 1px solid transparent;
        border-radius: {RADIUS_BTN}px;
        padding: 5px 16px;
        font-weight: bold;
    }}
    QPushButton:hover {{ background: {C['accent_deep']}; }}
    QPushButton:pressed {{ background: {C['ink']}; }}
    QPushButton:disabled {{ background: {C['line_soft']}; color: {C['ink_soft']}; }}
    """


def button_outline() -> str:
    """次按钮（描边）。"""
    return f"""
    QPushButton {{
        background: transparent; color: {C['ink']};
        border: {BORDER}px solid {C['line_soft']};
        border-radius: {RADIUS_BTN}px;
        padding: 5px 16px;
    }}
    QPushButton:hover {{ background: {C['hover']}; }}
    QPushButton:pressed {{ background: {C['line_soft']}; }}
    """
