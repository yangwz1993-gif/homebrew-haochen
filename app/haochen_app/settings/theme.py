"""visual-spec.md v1.0 的 token 落 Qt：色板常量 + QSS。

全局仅用这一套 token，禁止 UI 里硬编码色值；暗色主题后续在 token 层映射。
"""

# ── 色板（visual-spec §1）────────────────────────────────────
from ..appearance import BUTTON_RADIUS, CARD_RADIUS, COLORS, FONTS, INPUT_RADIUS

COLOR_BG = COLORS["bg"]
COLOR_SURFACE = COLORS["surface"]
COLOR_INK = COLORS["ink"]
COLOR_INK_SOFT = COLORS["ink_soft"]
COLOR_LINE = COLORS["line"]
COLOR_LINE_SOFT = COLORS["line_soft"]
COLOR_ACCENT = COLORS["accent"]
COLOR_INFO = COLORS["info"]
COLOR_WARN = COLORS["warn"]
COLOR_DANGER = COLORS["danger"]

# ── 字号（visual-spec §3）────────────────────────────────────
FONT_TITLE = 15
FONT_BODY = 14
FONT_BODY_SM = 12.5
FONT_CODE = 13

# ── 圆角 / 描边（visual-spec §2）─────────────────────────────
RADIUS_CARD = CARD_RADIUS
RADIUS_BUTTON = BUTTON_RADIUS
RADIUS_INPUT = INPUT_RADIUS
BORDER_MAIN = f"1px solid {COLOR_LINE_SOFT}"
BORDER_SOFT = f"1px solid {COLOR_LINE_SOFT}"

APP_QSS = f"""
QWidget {{
    background: {COLOR_BG};
    color: {COLOR_INK};
    font-family: {FONTS['family']};
    font-size: {FONT_BODY}px;
}}
QLabel {{ background: transparent; }}
QFrame#card {{
    background: {COLOR_SURFACE};
    border: {BORDER_MAIN};
    border-radius: {RADIUS_CARD}px;
}}
QLabel#cardTitle {{
    font-size: {FONT_TITLE}px;
    font-weight: 600;
}}
QLabel#hint {{
    color: {COLOR_INK_SOFT};
    font-size: {FONT_BODY_SM}px;
}}
QLabel#badgeOk {{
    color: {COLOR_ACCENT};
    font-size: {FONT_BODY_SM}px;
    font-weight: 600;
}}
QLabel#badgeOff {{
    color: {COLOR_INK_SOFT};
    font-size: {FONT_BODY_SM}px;
    font-weight: 600;
}}
QLabel#badgeErr {{
    color: {COLOR_DANGER};
    font-size: {FONT_BODY_SM}px;
    font-weight: 600;
}}
QLabel#statusOk  {{ color: {COLOR_ACCENT}; font-size: {FONT_BODY_SM}px; }}
QLabel#statusWarn {{ color: {COLOR_WARN}; font-size: {FONT_BODY_SM}px; }}
QLabel#statusErr {{ color: {COLOR_DANGER}; font-size: {FONT_BODY_SM}px; }}
QLineEdit {{
    background: {COLOR_BG};
    border: {BORDER_MAIN};
    border-radius: {RADIUS_INPUT}px;
    padding: 6px 10px;
    selection-background-color: {COLOR_ACCENT};
}}
QLineEdit:focus {{ border-color: {COLOR_ACCENT}; }}
QComboBox {{
    background: {COLOR_BG};
    border: {BORDER_MAIN};
    border-radius: {RADIUS_BUTTON}px;
    padding: 5px 10px;
    min-height: 22px;
}}
QComboBox:focus {{ border-color: {COLOR_ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox QAbstractItemView {{
    background: {COLOR_SURFACE};
    border: {BORDER_MAIN};
    border-radius: {RADIUS_BUTTON}px;
    selection-background-color: {COLOR_ACCENT};
    selection-color: {COLOR_SURFACE};
    outline: none;
}}
QPushButton {{
    background: {COLOR_SURFACE};
    border: {BORDER_MAIN};
    border-radius: {RADIUS_BUTTON}px;
    padding: 6px 14px;
}}
QPushButton:hover {{ background: {COLORS['hover']}; }}
QPushButton:pressed {{ background: {COLOR_LINE_SOFT}; }}
QPushButton#primary, QPushButton#primaryBtn {{
    background: {COLOR_ACCENT};
    color: {COLOR_SURFACE};
    font-weight: 600;
    border-color: transparent;
}}
QPushButton#primary:hover, QPushButton#primaryBtn:hover {{ background: {COLORS['accent_deep']}; }}
QPushButton:disabled {{ color: {COLOR_INK_SOFT}; background: {COLORS['hover']}; }}
QPushButton#connected {{ color: {COLOR_ACCENT}; background: {COLORS['accent_tint']}; border-color: transparent; }}
QPushButton#danger {{
    color: {COLOR_DANGER};
    border-color: {COLOR_DANGER};
}}
QToolButton {{
    background: transparent;
    border: none;
}}
QToolButton#moreButton {{
    min-width: 30px;
    max-width: 30px;
    min-height: 30px;
    max-height: 30px;
    padding: 0;
    border-radius: 10px;
    font-size: 20px;
}}
QToolButton#moreButton::menu-indicator {{ image: none; width: 0px; }}
QToolButton#moreButton:hover {{ background: {COLORS['hover']}; }}
QToolButton {{
    color: {COLOR_INK_SOFT};
    font-size: {FONT_BODY_SM}px;
}}
QToolButton:hover {{ color: {COLOR_INK}; }}
QMenu {{ background: {COLOR_SURFACE}; border: {BORDER_SOFT}; padding: 4px; }}
QMenu::item {{ padding: 7px 18px; }}
QMenu::item:selected {{ background: {COLORS['hover']}; }}
QScrollArea {{ border: none; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {COLOR_LINE_SOFT}; border-radius: 4px; min-height: 24px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
/* v0.1.4 §2：原生对话框去系统灰，统一米白卡片化（按钮沿用上方 QPushButton 规则） */
QMessageBox {{ background: {COLOR_BG}; }}
QMessageBox QLabel {{ color: {COLOR_INK}; background: transparent; }}
"""
