"""桌宠状态机 —— 落地 docs/interaction-spec.md §1。

IDLE → LISTENING → ACKNOWLEDGING → (PERCEIVING) → (ACTING)
     → COMPOSING → PRESENTING → LISTENING / IDLE

任何状态可被打断（Esc abort）；AWAKE 失焦不自动收起。
姿态映射（visual-spec §4 三姿态）：
  idle      ← IDLE / LISTENING / PRESENTING（安静待着、倾听或展示结果）
  thinking  ← ACKNOWLEDGING / PERCEIVING / ACTING / COMPOSING（处理中）
  alert     ← 出错（非状态，瞬时姿态，由 PetApp 触发后自动回到 idle）
"""

from __future__ import annotations

from enum import Enum


class PetState(Enum):
    IDLE = "IDLE"                          # 桌宠安静待着
    LISTENING = "LISTENING"                # 气泡唤起，可输入/追问
    ACKNOWLEDGING = "ACKNOWLEDGING"        # 请求已送达，给用户即时回执
    PERCEIVING = "PERCEIVING"              # 等待感知授权
    ACTING = "ACTING"                      # 真实工具执行中
    COMPOSING = "COMPOSING"                # 已收到模型输出事件，组织回答
    PRESENTING = "PRESENTING"              # 简答正在展示，等待退场或展开
    CANCELLED = "CANCELLED"                # 用户已停止，展示停止前已完成的内容

    # 兼容 0.2.x 内部调用；新代码与遥测统一使用上面的产品语义。
    AWAKE = LISTENING
    PERCEIVE = PERCEIVING
    ACT = ACTING
    THINK = COMPOSING
    CONVERGE = PRESENTING


# 状态 → 姿态贴图名（assets/pet/<pose>.png）
POSE_FOR_STATE = {
    PetState.IDLE: "idle",
    PetState.LISTENING: "idle",
    PetState.ACKNOWLEDGING: "thinking",
    PetState.PERCEIVING: "thinking",  # 专用动作贴图在后续视觉批次接入
    PetState.ACTING: "thinking",
    PetState.COMPOSING: "thinking",
    PetState.PRESENTING: "idle",
    PetState.CANCELLED: "idle",
}

POSE_ALERT = "angry"  # 出错姿态（visual-spec §4 angry/alert）

# 状态 → 气泡状态条文案（body-sm / color-info 弱化展示）
STATUS_LINE = {
    PetState.ACKNOWLEDGING: "收到，我接住了",
    PetState.PERCEIVING: "需要你确认后，我才能看屏幕",
    PetState.ACTING: "正在处理",
    PetState.COMPOSING: "正在组织回答",
    PetState.PRESENTING: "马上说重点",
}
