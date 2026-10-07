#!/usr/bin/env python
"""
探针：`build` 报告里到底有没有那行「语句原文真迹覆盖率」（CST，D3）。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.pipeline.build import build_project  # noqa: E402

REAL = r'D:\千连万理\RenPyTest\sr_general'


def main() -> int:
    out = Path(tempfile.mkdtemp(prefix='sgcstrep_')) / 'out'
    res = build_project(Path(REAL), out_dir=out, engine='renpy', strict=False)
    text = res.summary()
    print(text)
    print('---')
    guard = (res.report.get('build', {}) or {}).get('guard') or {}
    print('CST 那一行在里面吗：',
          '真迹覆盖率' in str(guard.get('statements') or ''))
    # 直接算一遍，看有没有异常
    from storygraph.core.cst import cst_coverage, format_cst_report
    from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
    from storygraph.parsing.discover import discover_scripts
    pr, _ = IncrementalRenPyAnalyzer().analyze(
        files=discover_scripts(Path(REAL)), graph=None)
    stats = cst_coverage(pr.statements)
    print('stats:', stats)
    print(format_cst_report(stats, '语句'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
