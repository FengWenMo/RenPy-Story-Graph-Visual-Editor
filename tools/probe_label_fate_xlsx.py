#!/usr/bin/env python
"""
真实项目 · **完整 Excel 往返**后的 label 集合。

比 `probe_label_fate_real.py` 严一档：那条只走 parse → render，
这一条走 parse → **xlsx → 读回** → render。

为什么必须多这一档：`Beat.is_empty()` 的"空行跳过"只在读表时生效
（`_read_beats_from_ws`）。2026-10-05 的空 label 修复里，
"补空拍"和"让空拍不被当空行丢掉"是**两处独立的代码**，
少了后一处，前面全白做 —— 而变异验证正是这么发现的。

只读。
"""
from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
from storygraph.story.beat_build import beats_from_parse_result
from storygraph.story.excel_export import export_beats_to_excel
from storygraph.story.excel_import import import_beats_from_excel
from storygraph.story.beat_render import render_beats_to_rpy

PROJECT = Path(r'D:\千连万理\RenPyTest\sr_general')
LABEL_IN_RPY = re.compile(r'^label\s+([^\s:#]+)', re.M)


def main() -> int:
    if not PROJECT.exists():
        print(f'（真实项目不在：{PROJECT} → 跳过）')
        return 0
    files = sorted((PROJECT / 'game').rglob('*.rpy'))
    lost_total, src_total, out_total = [], 0, 0
    extra = []
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
                out = render_beats_to_rpy(back)
            except Exception as e:                     # noqa: BLE001
                print(f'  ! {f.name}: {type(e).__name__}: {e}')
                continue
            src_labels = set(pr.labels)
            out_labels = set(LABEL_IN_RPY.findall(out))
            src_total += len(src_labels)
            out_total += len(out_labels)
            for name in sorted(src_labels - out_labels):
                lost_total.append(f'{f.name}::{name}')
            for name in sorted(out_labels - src_labels):
                extra.append(f'{f.name}::{name}')
    print(f'真实项目 {PROJECT}（逐文件 · 走完整 xlsx 往返）')
    print(f'  文件            {len(files)}')
    print(f'  源 label        {src_total}')
    print(f'  往返后 label    {out_total}')
    print(f'  丢了            {len(lost_total)}')
    for x in lost_total[:15]:
        print('    ·', x)
    print(f'  多出            {len(extra)}')
    for x in extra[:15]:
        print('    ·', x)
    if not lost_total:
        print('  → label 集合经 Excel 往返后完全一致')
    return 1 if lost_total else 0


if __name__ == '__main__':
    raise SystemExit(main())
