"""色板 / 描边 / 圆角 token —— 直接映射 docs/visual-spec.md §1/§2/§3。

三 UI 共用同一套 token；桌宠模块自持一份（避免与并行模块文件重叠），
P4 集成时应收敛到壳级共享 theme 模块。
"""

COLOR_BG = "#fafafa"        # 单层中性背景
COLOR_SURFACE = "#fafafa"   # 气泡内外同色
COLOR_INK = "#2b2b22"       # color-ink 主文字 / 深描边
COLOR_INK_SOFT = "#5c5c50"  # color-ink-soft 次级文字
COLOR_LINE = "#2b2b22"      # color-line 主描边
COLOR_LINE_SOFT = "#d7d9d7" # color-line-soft 分割线 / 弱描边
COLOR_ACCENT = "#3a7d5c"    # color-accent 强调（成功/确认）
COLOR_ACCENT_DEEP = "#2f6a4d"  # accent hover 加深（醒目绿按钮悬停）
COLOR_ACCENT_TINT = "#e4efe8"  # accent 极浅 tint（用户消息底，无描边区分用）
COLOR_INFO = "#5b7db1"      # color-info 信息/感知状态
COLOR_WARN = "#b8860b"      # color-warn 警告（需要确认）
COLOR_DANGER = "#b04a3a"    # color-danger 错误 / alert 姿态

BORDER_MAIN = 1.5   # 主描边 px
RADIUS_BUBBLE = 16  # 气泡/卡片圆角
RADIUS_BUTTON = 8   # 小标签/按钮
RADIUS_INPUT = 12   # 输入框
TAIL_SIZE = 14      # 气泡尾巴 ~14×14
# 气泡窗口底部仍有约 2px 到自绘尾尖；全身人物 PNG 顶部约有 8px 透明边。
# 窗口轻微重叠只发生在透明区，肉眼看到的“尾尖 → 发顶”仍约为 8px。
TAIL_TIP_BOTTOM_INSET = 2
PET_VISIBLE_TOP_INSET = 8
BUBBLE_PET_GAP = -2

FONT_FAMILY = '-apple-system, "PingFang SC", "SF Pro", sans-serif'
FONT_TITLE = 15   # title
FONT_BODY = 14    # body
FONT_BODY_SM = 12.5  # body-sm

# 动效节奏（visual-spec §5 MVP 级）
ANIM_SUMMON_MS = 200    # 唤起气泡 淡入+上滑（180–220）
ANIM_BLOCK_MS = 180     # 对话流逐条弹出
ANIM_POSE_MS = 200      # 姿态切换轻过渡（160–220，淡出100+淡入100）
ANIM_DISMISS_MS = 150   # 收起 淡出
