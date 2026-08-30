#!/bin/bash
# haochen 全量回归（P6）：L1 引擎协议 / L2 单元 / L3 UI / L3.5 真链路 / L4 分发构建
# 用法: bash tests/run_all_regressions.sh [--real]
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 1
APP="$ROOT/app"
VENV="$APP/.venv/bin/python"
REAL=0; [ "${1:-}" = "--real" ] && REAL=1
PASS=0
FAIL=0

run_name() { echo; echo "=== $1 ==="; shift; }

check() { # $1=label; exit code of previous command
  local label="$1" rc="$?"
  if [ "$rc" -eq 0 ]; then echo "OK   $label"; PASS=$((PASS + 1)); else echo "FAIL $label"; FAIL=$((FAIL + 1)); fi
}

# L1
run_name "L1 mock driver (45) + ext 看图模式 (21) + markdown 清洗 (11)"
bash -c "cd '$ROOT/mock-engine' && python3 driver.py | tail -1 | grep -q '45/45'" && \
bash -c "cd '$ROOT' && NODE_PATH='$ROOT/pi-source/node_modules' bun run app/ext/test-visual-mode.ts | tail -1 | grep -q '21/21'" && \
bash -c "cd '$APP' && QT_QPA_PLATFORM=offscreen '$VENV' haochen_app/chat/verification/test_markdown_sanitize.py 2>/dev/null | tail -1 | grep -q '11/11'"
check "L1 mock driver (45) + ext 看图模式 (21) + markdown 清洗 (11)"

# L2
run_name "L2 settings selfcheck (26)"
bash -c "cd '$APP' && QT_QPA_PLATFORM=offscreen '$VENV' -m haochen_app.settings.selfcheck 2>/dev/null | tail -1 | grep -q '26/26'"
check "L2 settings selfcheck (26)"

# L3
run_name "L3 M-C chat (5)"
bash -c "HAOCHEN_MOCK=1 MOCK_TICK_MS=5 QT_QPA_PLATFORM=offscreen '$VENV' '$APP/haochen_app/chat/verification/run_scenarios.py' 2>/dev/null | tail -1 | grep -q '全部场景完成'"
check "L3 M-C chat (5)"

run_name "L3 M-E pet (17)"
bash -c "MOCK_TICK_MS=20 QT_QPA_PLATFORM=offscreen '$VENV' '$APP/haochen_app/pet/verification/run_scenarios.py' 2>/dev/null | grep -q 'S5 气泡已收起'"
check "L3 M-E pet (17)"

run_name "L3 P4 集成 (25)"
bash -c "HAOCHEN_MOCK=1 MOCK_TICK_MS=30 QT_QPA_PLATFORM=offscreen HAOCHEN_HOME=/tmp/haochen-p6-test HAOCHEN_AUTO_IMPORT_KEY=1 HAOCHEN_AUTO_RESTART=1 '$VENV' '$APP/verification/p4_integration.py' 2>/dev/null | grep -q '25/25'"
check "L3 P4 集成 (25)"

if [ "$REAL" = "1" ]; then
  run_name "L3.5 真引擎全链路 (16)"
  bash -c "HAOCHEN_HOME=/tmp/haochen-p6-real HAOCHEN_AUTO_IMPORT_KEY=1 QT_QPA_PLATFORM=offscreen '$VENV' '$APP/verification/p4_real_chain.py' 2>/dev/null | grep -q '16/16'"
  check "L3.5 真引擎全链路 (16)"
fi

# L4
run_name "L4 packaging build (dmg)"
HAOCHEN_BUILD_MODE=development bash packaging/build.sh >/dev/null 2>&1
check "L4 packaging build (dmg)"

echo
echo "==== 回归结果: PASS=$PASS FAIL=$FAIL ===="
exit "$FAIL"
