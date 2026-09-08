#!/bin/bash
# 从仓库运行已构建的开发 App，不安装、不覆盖正式版配置或 Keychain 条目。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
APP="$PROJECT_ROOT/packaging/dist/haochen.app"
EXECUTABLE="$APP/Contents/MacOS/haochen"

if [ ! -x "$EXECUTABLE" ]; then
  echo "开发 App 尚未构建。请先运行："
  echo "HAOCHEN_BUILD_MODE=development bash packaging/build.sh"
  exit 1
fi

if [ "${HAOCHEN_MOCK:-0}" = "1" ]; then
  echo "冻结开发包不包含 mock 引擎；请直接运行内嵌真实引擎，或从源码运行 mock。"
  exit 2
fi

export HAOCHEN_HOME="${HAOCHEN_DEV_HOME:-$HOME/Library/Application Support/haochen-development}"
export HAOCHEN_KEYCHAIN_SERVICE="${HAOCHEN_DEV_KEYCHAIN_SERVICE:-com.haochen.app.development.api-key}"
# ad-hoc 开发签名不参与正式版 TCC 指纹治理，避免清除正式 App 的授权记录。
export HAOCHEN_SKIP_PERMISSION_GUIDE=1

mkdir -p "$HAOCHEN_HOME"
chmod 700 "$HAOCHEN_HOME"
exec "$EXECUTABLE" "$@"
