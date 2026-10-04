#!/usr/bin/env python
"""真实项目：产物里少了哪几个 `transform` 定义？是丢了还是去重？

`probe_toplevel_real.py` 量出 transform 157 → 148（-9）。
这一档把**名字**列出来，并区分两种可能：

  · 顶层区间（`TopLevelRegionNode`）**漏收** → 真丢；
  · 被 `collect_used_transforms` 有意只输出"用到的" → 不是丢。

只读。
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
TF = re.compile(r'^transform\s+([A-Za-z_0-9]+)\s*:', re.M)


def main() -> int:
    files = sorted((PROJECT / 'game').rglob('*.rpy'))
    src = {}
    for f in files:
        for i, ln in enumerate(f.read_text(encoding='utf-8',
                                           errors='replace').splitlines(), 1):
            m = TF.match(ln)
            if m:
                src.setdefault(m.group(1), []).append(f'{f.name}:{i}')

    pr, _g = IncrementalRenPyAnalyzer().analyze(files=files, graph=None)
    beats = beats_from_parse_result(pr)
    out = render_beats_to_rpy(beats)
    got = set(TF.findall(out))

    missing = sorted(set(src) - got)
    print(f'源 transform {len(src)}   产物 {len(got)}   缺 {len(missing)}')
    for name in missing[:20]:
        print(f'  ✗ {name:28} 定义于 {src[name][:2]}')
    if not missing:
        print('  → 一个不缺')

    # 顶层区间到底收了多少 transform？
    fb = '\n'.join(b.code for b in (getattr(pr, 'file_blocks', None) or [])
                   ) + '\n'.join(getattr(b, 'file_blocks', '')
                                 for b in beats)
    in_fb = len(set(TF.findall(fb)))
    print(f'\nfile_blocks 里收进的 transform：{in_fb}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
