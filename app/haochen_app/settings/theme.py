"""visual-spec.md v1.0 的 token 落 Qt：色板常量 + QSS。

全局仅用这一套 token，禁止 UI 里硬编码色值；暗色主题后续在 token 层映射。
"""

# ── 色板（visual-spec §1）────────────────────────────────────
COLOR_BG = "#fdf6e3"        # 主背景（米白）
COLOR_SURFACE = "#fffaf0"   # 卡片表面
COLOR_INK = "#2b2b22"       # 主文字 / 深描边
COLOR_INK_SOFT = "#5c5c50"  # 次级文字
COLOR_LINE = "#2b2b22"      # 描边（主）
COLOR_LINE_SOFT = "#c8c0ac" # 分割线 / 弱描边
COLOR_ACCENT = "#3a7d5c"    # 强调（成功/确认）
COLOR_INFO = "#5b7db1"      # 信息
COLOR_WARN = "#b8860b"      # 警告
COLOR_DANGER = "#b04a3a"    # 错误

# ── 字号（visual-spec §3）────────────────────────────────────
FONT_TITLE = 15
FONT_BODY = 14
FONT_BODY_SM = 12.5
FONT_CODE = 13

# ── 圆角 / 描边（visual-spec §2）─────────────────────────────
RADIUS_CARD = 16
RADIUS_BUTTON = 8
RADIUS_INPUT = 12
BORDER_MAIN = f"2px solid {COLOR_LINE}"
BORDER_SOFT = f"1px solid {COLOR_LINE_SOFT}"

APP_QSS = f"""
QWidget {{
    background: {COLOR_BG};
    color: {COLOR_INK};
    font-family: -apple-system, "PingFang SC", "SF Pro", sans-serif;
    font-size: {FONT_BODY}px;
}}
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
    color: {COLOR_WARN};
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
QPushButton:hover {{ background: {COLOR_BG}; }}
QPushButton:pressed {{ background: {COLOR_LINE_SOFT}; }}
QPushButton#primary, QPushButton#primaryBtn {{
    background: {COLOR_ACCENT};
    color: {COLOR_SURFACE};
    font-weight: 600;
}}
QPushButton#primary:hover, QPushButton#primaryBtn:hover {{ background: #2f6b4d; }}
QPushButton#danger {{
    color: {COLOR_DANGER};
    border-color: {COLOR_DANGER};
}}
QToolButton {{
    background: transparent;
    border: none;
    color: {COLOR_INK_SOFT};
    font-size: {FONT_BODY_SM}px;
}}
QToolButton:hover {{ color: {COLOR_INK}; }}
QScrollArea {{ border: none; }}
/* v0.1.4 §2：原生对话框去系统灰，统一米白卡片化（按钮沿用上方 QPushButton 规则） */
QMessageBox {{ background: {COLOR_BG}; }}
QMessageBox QLabel {{ color: {COLOR_INK}; background: transparent; }}
"""
