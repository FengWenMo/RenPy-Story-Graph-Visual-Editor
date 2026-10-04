#!/usr/bin/env python
"""真实项目口径：**产物里的 label 集合** 与 **源文件里的** 差多少？

比"空 label"更准——直接问解析器 + 渲染器：
  pr.labels（源） vs 渲染产物里的 `label X:`（产物）
差一个就是 jump 目标失效。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
from storygraph.story.beat_build import beats_from_parse_result
from storygraph.story.beat_render import render_beats_to_rpy

PROJECT = Path(r'D:\千连万理\RenPyTest\sr_general')
LABEL_IN_RPY = re.compile(r'^label\s+([^\s:#]+)', re.M)


def main() -> int:
    if not PROJECT.exists():
        print(f'（真实项目不在：{PROJECT} → 跳过）')
        return 0
    files = sorted((PROJECT / 'game').rglob('*.rpy'))
    pr, _g = IncrementalRenPyAnalyzer().analyze(files=files, graph=None)
    beats = beats_from_parse_result(pr)
    out = render_beats_to_rpy(beats)

    src_labels = set(pr.labels)
    out_labels = set(LABEL_IN_RPY.findall(out))
    print(f'真实项目 {PROJECT}')
    print(f'  文件            {len(files)}')
    print(f'  beats           {len(beats)}')
    print(f'  源 label        {len(src_labels)}')
    print(f'  产物 label      {len(out_labels)}')
    missing = sorted(src_labels - out_labels)
    extra = sorted(out_labels - src_labels)
    print(f'  丢了            {len(missing)}  {missing[:15]}')
    print(f'  多出            {len(extra)}  {extra[:15]}')
    if not missing and not extra:
        print('  → label 集合完全一致')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
