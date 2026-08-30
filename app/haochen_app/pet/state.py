"""桌宠状态机 —— 落地 docs/interaction-spec.md §1。

IDLE → AWAKE → (PERCEIVE) → THINK → (ACT) → CONVERGE → AWAKE → IDLE

任何状态可被打断（Esc abort）；AWAKE 失焦不自动收起。
姿态映射（visual-spec §4 三姿态）：
  idle      ← IDLE / AWAKE（安静待着、气泡挂着）
  thinking  ← PERCEIVE / THINK / ACT / CONVERGE（生成中）
  alert     ← 出错（非状态，瞬时姿态，由 PetApp 触发后自动回到 idle）
"""

from __future__ import annotations

from enum import Enum


class PetState(Enum):
    IDLE = "IDLE"            # 桌宠安静待着
    AWAKE = "AWAKE"          # 气泡唤起，可输入/追问
    PERCEIVE = "PERCEIVE"    # 感知：读屏提示 / 确认等待
    THINK = "THINK"          # 思考生成中
    ACT = "ACT"              # 工具执行中（读屏等）
    CONVERGE = "CONVERGE"    # 收敛：短结回合


# 状态 → 姿态贴图名（assets/pet/<pose>.png）
POSE_FOR_STATE = {
    PetState.IDLE: "idle",
    PetState.AWAKE: "idle",
    PetState.PERCEIVE: "thinking",   # 「眼睛对准屏幕」暂无专用贴图，用 thinking 代
    PetState.THINK: "thinking",
    PetState.ACT: "thinking",
    PetState.CONVERGE: "thinking",
}

POSE_ALERT = "angry"  # 出错姿态（visual-spec §4 angry/alert）

# 状态 → 气泡状态条文案（body-sm / color-info 弱化展示）
STATUS_LINE = {
    PetState.PERCEIVE: "我正看一下你的屏幕…",
    PetState.THINK: "正在想…",
    PetState.ACT: "正在读屏…",
    PetState.CONVERGE: "收敛成短结…",
}
