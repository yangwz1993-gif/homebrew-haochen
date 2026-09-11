"""Compact-bubble aliases for the shared application appearance tokens."""

from ..appearance import BUTTON_RADIUS, CARD_RADIUS, COLORS, FONTS, INPUT_RADIUS

COLOR_BG = COLORS["bg"]
COLOR_SURFACE = COLORS["surface"]
COLOR_INK = COLORS["ink"]
COLOR_INK_SOFT = COLORS["ink_soft"]
COLOR_LINE = COLORS["line"]
COLOR_LINE_SOFT = COLORS["line_soft"]
COLOR_ACCENT = COLORS["accent"]
COLOR_ACCENT_DEEP = COLORS["accent_deep"]
COLOR_ACCENT_TINT = COLORS["accent_tint"]
COLOR_INFO = COLORS["info"]
COLOR_WARN = COLORS["warn"]
COLOR_DANGER = COLORS["danger"]

BORDER_MAIN = 1.5   # 主描边 px
RADIUS_BUBBLE = CARD_RADIUS
RADIUS_BUTTON = BUTTON_RADIUS
RADIUS_INPUT = INPUT_RADIUS
TAIL_SIZE = 14      # 气泡尾巴 ~14×14
# 气泡窗口底部仍有约 2px 到自绘尾尖；全身人物 PNG 顶部约有 8px 透明边。
# 窗口轻微重叠只发生在透明区，肉眼看到的“尾尖 → 发顶”仍约为 8px。
TAIL_TIP_BOTTOM_INSET = 2
PET_VISIBLE_TOP_INSET = 8
BUBBLE_PET_GAP = -2

FONT_FAMILY = FONTS["family"]
FONT_TITLE = FONTS["title"]
FONT_BODY = FONTS["body"]
FONT_BODY_SM = FONTS["body_sm"]

# 动效节奏（visual-spec §5 MVP 级）
ANIM_SUMMON_MS = 200    # 唤起气泡 淡入+上滑（180–220）
ANIM_BLOCK_MS = 180     # 对话流逐条弹出
ANIM_POSE_MS = 200      # 姿态切换轻过渡（160–220，淡出100+淡入100）
ANIM_DISMISS_MS = 150   # 收起 淡出
