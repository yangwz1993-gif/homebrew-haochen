"""Test-integrity ratchet for the quality gate (防作弊修复/防破坏修复).

锚定一个基线（tests/baseline.json），之后每次 `make check` 校验：

1. 测试文件只增不删（tests/test_*.py 与 tests/*.test.mjs）；
2. pytest 收集到的用例数只增不减；
3. skip/xfail 标记总数只增不减（防"加水"豁免失败用例）；
4. 覆盖率总分不降；单文件覆盖率下降超过 1.0pp 视为回退。

用法：
    # 冻结基线（版本开工时跑一次，之后不再更新）
    uv run python scripts/check_test_integrity.py --write-baseline

    # 常规校验（quality.sh 在 pytest --cov 之后调用，此时 coverage.xml 已存在）
    uv run python scripts/check_test_integrity.py
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "tests" / "baseline.json"
COVERAGE_XML = ROOT / "coverage.xml"
TESTS_DIR = ROOT / "tests"

# 单文件覆盖率允许的最大下探（百分点）：防浮点噪声，超出即视为真回退。
FILE_DROP_TOLERANCE_PP = 1.0
# 总覆盖率允许的噪声带（百分点）：branch coverage 在两次全绿运行间有约 0.02pp 波动。
TOTAL_DROP_TOLERANCE_PP = 0.1

_SKIP_PATTERNS = (
    re.compile(r"pytest\.mark\.(skip|skipif|xfail)\b"),
    re.compile(r"pytest\.skip\("),
)


def _test_files() -> list[str]:
    files = sorted(TESTS_DIR.glob("test_*.py")) + sorted(TESTS_DIR.glob("*.test.mjs"))
    return [str(p.relative_to(ROOT)) for p in files]


def _skip_marker_count() -> int:
    total = 0
    for path in TESTS_DIR.glob("test_*.py"):
        text = path.read_text(encoding="utf-8")
        for pattern in _SKIP_PATTERNS:
            total += len(pattern.findall(text))
    return total


def _collected_count() -> int:
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "--no-header"],
        cwd=ROOT, capture_output=True, text=True, timeout=300, check=False,
    )
    # 末行形如 "813 tests collected in 3.2s" / "1 test collected"
    match = re.search(r"(\d+) tests? collected", out.stdout)
    if not match:
        raise RuntimeError(f"无法解析 pytest 收集数：{out.stdout[-300:]} {out.stderr[-300:]}")
    return int(match.group(1))


def _coverage() -> tuple[float, dict[str, float]]:
    if not COVERAGE_XML.exists():
        raise RuntimeError("coverage.xml 不存在：请先运行 pytest --cov（quality.sh 已保证顺序）")
    tree = ET.parse(COVERAGE_XML)
    root = tree.getroot()
    total = round(float(root.attrib["line-rate"]) * 100, 2)
    files: dict[str, float] = {}
    for cls in root.iter("class"):
        filename = cls.attrib.get("filename", "")
        if filename.startswith("app/haochen_app/"):
            files[filename] = round(float(cls.attrib.get("line-rate", "0")) * 100, 2)
    return total, files


def collect_baseline() -> dict:
    total, files = _coverage()
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    return {
        "createdAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "version": version,
        "pytestCollected": _collected_count(),
        "testFiles": _test_files(),
        "skipMarkers": _skip_marker_count(),
        "coverage": {"total": total, "files": files},
    }


def check(baseline: dict) -> list[str]:
    failures: list[str] = []

    # 1. 测试文件只增不删
    missing = sorted(set(baseline["testFiles"]) - set(_test_files()))
    if missing:
        failures.append(f"测试文件被删除（{len(missing)} 个）：{missing}")

    # 2. 用例数只增不减
    before, after = int(baseline["pytestCollected"]), _collected_count()
    if after < before:
        failures.append(f"pytest 收集用例数减少：{before} → {after}（删除/豁免用例即破坏）")

    # 3. skip/xfail 标记只增不减
    skip_before, skip_after = int(baseline["skipMarkers"]), _skip_marker_count()
    if skip_after > skip_before:
        failures.append(f"skip/xfail 标记增多：{skip_before} → {skip_after}（不得用豁免掩盖失败）")

    # 4. 覆盖率：总分不降，单文件下探超阈值即回退
    total_now, files_now = _coverage()
    total_before = float(baseline["coverage"]["total"])
    if total_now < total_before - TOTAL_DROP_TOLERANCE_PP:
        failures.append(f"总覆盖率下降：{total_before}% → {total_now}%")
    for filename, rate_before in baseline["coverage"]["files"].items():
        rate_now = files_now.get(filename)
        if rate_now is None:
            failures.append(f"覆盖率文件消失：{filename}")
        elif rate_now < rate_before - FILE_DROP_TOLERANCE_PP:
            failures.append(f"文件覆盖率回退：{filename} {rate_before}% → {rate_now}%")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="test integrity ratchet")
    parser.add_argument("--write-baseline", action="store_true", help="冻结当前状态为新基线")
    args = parser.parse_args()

    if args.write_baseline:
        baseline = collect_baseline()
        BASELINE.write_text(json.dumps(baseline, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
        print(f"baseline 已冻结：{BASELINE.relative_to(ROOT)} "
              f"（{baseline['pytestCollected']} 个用例，总覆盖率 {baseline['coverage']['total']}%）")
        return 0

    if not BASELINE.exists():
        print(f"缺少基线 {BASELINE.relative_to(ROOT)}；先以 --write-baseline 冻结", file=sys.stderr)
        return 2
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    failures = check(baseline)
    if failures:
        print("test integrity 校验失败（疑似作弊/破坏式修复）：", file=sys.stderr)
        for item in failures:
            print(f"  ✗ {item}", file=sys.stderr)
        return 1
    print(f"test integrity 通过（基线 {baseline['version']}，{baseline['pytestCollected']} 用例锚定）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
