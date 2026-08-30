#!/bin/bash
# Packaging spike 一键构建：venv → 依赖 → PyInstaller → LSUIElement → ad-hoc 签名
# 用法: bash build.sh
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

UV="${UV:-$HOME/.local/bin/uv}"
PY_VERSION="3.12"
APP_NAME="SpikeProbe"

# 1. 独立 Python（装在 spike 目录内，不碰系统/全局）
export UV_PYTHON_INSTALL_DIR="$DIR/.python"
if [ ! -x "$DIR/.python/$PY_VERSION"*/bin/python3 ] 2>/dev/null; then
    "$UV" python install "$PY_VERSION"
fi

# 2. venv + 依赖
[ -d "$DIR/.venv" ] || "$UV" venv .venv --python "$PY_VERSION"
"$UV" pip install --python .venv/bin/python pyinstaller pyqt6

# 3. PyInstaller --onedir --windowed 直出 .app
#    --add-data 用于记录 data 文件在 PyInstaller 6 onedir .app 中的落点（见 SPIKE-RESULT.md）
.venv/bin/pyinstaller --noconfirm --clean --onedir --windowed \
    --name "$APP_NAME" \
    --add-data "fake-engine.sh:." \
    app.py

APP="$DIR/dist/$APP_NAME.app"

# 4. 按需求把假引擎放进 Contents/Resources/
cp "$DIR/fake-engine.sh" "$APP/Contents/Resources/fake-engine"
chmod +x "$APP/Contents/Resources/fake-engine"

# 5. Info.plist 注入 LSUIElement=true（Accessory 形态，无 Dock 图标）
PLIST="$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Delete :LSUIElement" "$PLIST" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :LSUIElement bool true" "$PLIST"

# 6. ad-hoc 签名（改了 Info.plist 和 Resources 后必须重签）
codesign --force --deep --sign - "$APP"
codesign --verify --deep --strict "$APP"

echo "==> 构建完成: $APP"
echo "==> 运行: open \"$APP\""
