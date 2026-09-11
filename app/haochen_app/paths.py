"""冻结（PyInstaller .app）/ 开发两形态的资源路径解析（P5）。

冻结形态 Resources 布局（packaging/build.sh 保证）：
    Contents/Resources/
    ├── engine/haochen-engine      # Bun 单文件引擎
    ├── haochen-reader             # 指向 reader/haochen-reader（onedir 常驻服务）
    ├── ext/index.ts               # 引擎扩展（read_screen + 分层结果协议）
    ├── config/                    # 配置模板（models/settings/auth.json.template）
    └── assets/pet/*.png           # 桌宠姿态图
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]  # 仓库根（开发形态）


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def resources_dir() -> Path:
    """资源根：冻结=Contents/Resources；开发=仓库根。"""
    if is_frozen():
        return Path(sys.executable).resolve().parent.parent / "Resources"
    return PROJECT_ROOT


def engine_binary() -> Path:
    if is_frozen():
        return resources_dir() / "engine" / "haochen-engine"
    return PROJECT_ROOT / "engine" / "haochen-engine"


def ext_entry() -> Path:
    if is_frozen():
        return resources_dir() / "ext" / "index.ts"
    return PROJECT_ROOT / "app" / "ext" / "index.ts"


def config_templates() -> Path:
    if is_frozen():
        return resources_dir() / "config"
    return PROJECT_ROOT / "config"


def pet_assets() -> Path:
    if is_frozen():
        return resources_dir() / "assets" / "pet"
    return PROJECT_ROOT / "app" / "assets" / "pet"


def reader_binary() -> Path | None:
    """内嵌读屏执行体；不存在返回 None（扩展回退 ~/.local/bin/haochen 开发路径）。"""
    p = (resources_dir() / "haochen-reader" if is_frozen()
         else PROJECT_ROOT / "app" / "reader" / "reader-run.sh")
    return p if p.exists() else None


def mock_engine() -> Path:
    """mock 引擎（仅开发形态存在）。"""
    return PROJECT_ROOT / "mock-engine" / "mock_engine.py"
