#!/usr/bin/env python
"""
真实项目：**工具自己走一遍 .rpy → xlsx → 读回**，W114 应该是 **0**。

为什么必须跑这一档：W114 是"你哪几格脏了"的提示，它的价值全在**可信**。
如果工具自己写出去的表读回来就报 W114，那这条提示会变成常态噪音，
人就不看了 —— 提示一多等于没有（"干净时一句话都不多"也是同一个道理）。

只读。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
from storygraph.story.beat_build import beats_from_parse_result
from storygraph.story.excel_export import export_beats_to_excel
from storygraph.story.excel_import import import_beats_from_excel
from storygraph.pipeline.validate import validate_level1

PROJECT = Path(r'D:\千连万理\RenPyTest\sr_general')


def main() -> int:
    if not PROJECT.exists():
        print(f'（真实项目不在：{PROJECT} → 跳过）')
        return 0
    files = sorted((PROJECT / 'game').rglob('*.rpy'))
    total_beats = 0
    w114 = []
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for i, f in enumerate(files):
            try:
                pr, _g = IncrementalRenPyAnalyzer().analyze(files=[f],
                                                            graph=None)
                beats = beats_from_parse_result(pr)
                xl = tmp / f'{i}.xlsx'
                export_beats_to_excel(beats, xl)
                back = import_beats_from_excel(xl)
            except Exception as e:                    # noqa: BLE001
                print(f'  ! {f.name}: {type(e).__name__}: {e}')
                continue
            total_beats += len(back)
            for issue in validate_level1(back):
                if issue.code == 'W114':
                    w114.append((f.name, issue.message[:70],
                                 str(issue.where or '')[:40]))
    print(f'真实项目 {PROJECT}（逐文件 · 工具自己往返）')
    print(f'  文件            {len(files)}')
    print(f'  往返后的拍      {total_beats}')
    print(f'  W114            {len(w114)}')
    for name, msg, where in w114[:10]:
        print(f'    · {name}: {msg} @ {where}')
    if not w114:
        print('  → 工具自己写出去的表读回来是干净的，W114 不会误报')
    return 1 if w114 else 0


if __name__ == '__main__':
    raise SystemExit(main())
