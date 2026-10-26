#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
从一次 fasttest/pytest 的 JUnit XML 重新生成 `seed.json`（维护者脚本）。
=====================================================================

日常跑测试**不需要**它：`run.py` 每轮会把真实耗时学习到
`.pip-tmp/fasttest/durations.json`，排序会自动变准。这个脚本只在下面这种
情况用：

  · 新机器 / 新同事第一次跑，想让"冷启动"就按重文件优先；
  · 测试文件大改（新增/删除了很重的文件），旧的 seed 失真；
  · 想重新录一份可提交的权重。

用法：

    python tools/fasttest/run.py                  # 先跑一轮
    python tools/fasttest/make_seed.py .pip-tmp/fasttest/runs/<...>/junit.xml

脚本只读 JUnit、只写 `tools/fasttest/seed.json`，不改测试代码。
"""
from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent
SEED_PATH = TOOL_DIR / 'seed.json'


def file_seconds(junit: Path) -> tuple[dict[str, float], float]:
    root = ET.parse(str(junit)).getroot()
    files: dict[str, float] = {}
    total = 0.0
    for case in root.iter('testcase'):
        try:
            seconds = float(case.get('time') or 0.0)
        except ValueError:
            seconds = 0.0
        rel = (case.get('file') or '').replace('\\', '/')
        if not rel or rel.startswith('/') or ':' in rel:
            # xdist 聚合的 JUnit 只有 classname：tests.test_foo.TestX
            parts = (case.get('classname') or '').split('.')
            if len(parts) >= 2 and parts[0] == 'tests':
                rel = f'tests/{parts[1]}.py'
            else:
                continue
        if case.find('skipped') is not None and seconds <= 0:
            continue
        files[rel] = files.get(rel, 0.0) + seconds
        total += seconds
    return files, total


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument('junit', help='JUnit XML 路径（run.py 的 run_dir/junit.xml）')
    parser.add_argument('--reference-seconds', type=float, default=0.0,
                        help='可选：串行全量墙钟秒数，仅用于汇总里的“参考”')
    args = parser.parse_args(argv)

    files, total = file_seconds(Path(args.junit))
    if not files:
        print('没有从 JUnit 里读到任何 testcase。')
        return 1
    files = {k: round(v, 3) for k, v in files.items() if v > 0}

    # 重录时默认保留旧的 "串行参考"（它来自 README/历史实测，不是本轮
    # 并行 JUnit 的耗时之和 —— 后者会被并行争抢放大/缩小，只适合排序）。
    old_reference = 0.0
    try:
        old_seed = json.loads(SEED_PATH.read_text(encoding='utf-8'))
        old_reference = float(old_seed.get('reference_seconds') or 0)
    except (OSError, ValueError, TypeError):
        pass
    reference = (args.reference_seconds if args.reference_seconds > 0
                 else (old_reference or total + 60.0))

    seed = {
        'version': 1,
        'generated': date.today().isoformat(),
        'note': ('每个测试文件的历史耗时（秒，来自一次并行全量运行；'
                 '只用于把重文件排到队首，不影响判据）。'
                 '由 tools/fasttest/make_seed.py 生成。'),
        'reference_seconds': round(reference, 1),
        'files': dict(sorted(files.items())),
    }
    SEED_PATH.write_text(
        json.dumps(seed, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'写入 {SEED_PATH}：{len(files)} 个文件，'
          f'总计 {total:.1f}s，参考 {seed["reference_seconds"]:.1f}s')
    print('最重的 10 个文件：')
    for rel, seconds in sorted(files.items(), key=lambda kv: -kv[1])[:10]:
        print(f'  {seconds:8.1f}s  {rel}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
