#!/usr/bin/env python
"""
真机：构建报告里"未声明的自定义语句"点名长什么样（只读，产物写临时目录）
==========================================================================

CST 第二步（设计文档 §5 第 2 步）：**缺声明就降级，并在报告里点名**。
这个探针把真机 `sr_general` 的构建报告里那一段打出来 ——
确认"工具不认识的东西"在生产路径上真的说得出口。

    python tools/probe_build_report_undeclared_real.py [项目根]
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.pipeline.build import build_project          # noqa: E402


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1
                else r'D:\千连万理\RenPyTest\sr_general')
    out = Path(tempfile.mkdtemp(prefix='probe_build_report_'))
    res = build_project(str(root), out_dir=out, write_report=False)
    guard = ((res.report.get('build') or {}).get('guard') or {})
    print(f'产物目录（临时）：{out}')
    print('--- 报告里的"语句"那一段 ---')
    print(guard.get('statements') or '（空）')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
