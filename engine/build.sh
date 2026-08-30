#!/usr/bin/env bash
# haochen-engine 一键构建脚本
# 从 pi-source（v0.84.3 monorepo 源码）用 Bun 打包出单文件可执行 haochen-engine。
# 用法: ./build.sh
set -euo pipefail

ENGINE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PI_SOURCE="$ENGINE_DIR/../pi-source"
CODING_AGENT="$PI_SOURCE/packages/coding-agent"

export PATH="$HOME/homebrew/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

if ! command -v bun >/dev/null 2>&1; then
    echo "bun 未安装，尝试用 brew 安装..."
    brew install bun
fi
echo "==> bun $(bun --version)"

if [ ! -d "$PI_SOURCE/node_modules" ]; then
    echo "==> 安装 pi-source 依赖 (npm install --ignore-scripts)..."
    (cd "$PI_SOURCE" && npm install --ignore-scripts)
fi

# 模型数据（packages/ai/src/providers/data/*.json + *.models.ts + models.generated.ts）
# 已包含在源码树中。若被删除，需重新生成：
#   cd packages/ai && node scripts/generate-models.ts
# 注意：generate-models.ts 直连 https://models.dev/api.json，不经代理；
# 网络受限环境可先 curl 下载 api.json，再参考 RESULT.md 的临时补丁方式生成。
if [ ! -f "$PI_SOURCE/packages/ai/src/providers/data/.manifest.json" ]; then
    echo "ERROR: packages/ai/src/providers/data 缺失，请先按 RESULT.md 说明重新生成模型数据。" >&2
    exit 1
fi

echo "==> bun build --compile ..."
cd "$CODING_AGENT"
# 入口与 worker 均来自 TS 源码；workspace 包通过根 tsconfig.json 的 paths 解析到各 packages/*/src，
# 无需先构建 dist。image-resize-worker 必须作为显式入口传入，否则不会嵌入二进制。
bun build --compile --no-compile-autoload-bunfig --no-compile-autoload-dotenv \
    --target=bun-darwin-arm64 \
    ./src/bun/cli.ts ./src/utils/image-resize-worker.ts \
    --outfile "$ENGINE_DIR/haochen-engine"
# bun 1.4 会在 cwd 留下临时 .*.bun-build 目录，清理之
rm -rf ./.*.bun-build

chmod +x "$ENGINE_DIR/haochen-engine"
echo "==> 完成: $ENGINE_DIR/haochen-engine"
ls -lh "$ENGINE_DIR/haochen-engine"
