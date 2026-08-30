"""haochen 桌宠模块（M-E：L0 桌宠 + L1 唤起气泡）。

入口：app/run_pet.py；装配见 app.py:PetApp；状态机见 state.py:PetState。
"""

from .app import PetApp
from .state import PetState

__all__ = ["PetApp", "PetState"]
