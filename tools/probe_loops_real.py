#!/usr/bin/env python
"""探针：真机里**语句级**循环有几处、分别在哪（用来判断影响面）。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.parsing.discover import discover_scripts
from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
from storygraph.story.beat_build import beats_from_parse_result

REAL = Path(r'D:\千连万理\RenPyTest\sr_general')


def main() -> int:
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=discover_scripts(REAL),
                                               graph=None)
    loops = [s for s in pr.statements if s.kind == 'loop']
    print(f'语句级循环 {len(loops)} 处：')
    for s in loops:
        print(f'  {Path(s.source[0]).name}:{s.source[1]}  {s.payload["header"]}'
              f'  walked={s.payload["walked"]}')
    body = [s for s in pr.statements
            if str(getattr(s, 'nest_path', '') or '').startswith('loop:')]
    print(f'块体语句 {len(body)} 条：')
    for s in body:
        print(f'  {Path(s.source[0]).name}:{s.source[1]}  {s.kind}  '
              f'{getattr(s, "nest_path", "")}')
    beats = beats_from_parse_result(pr)
    print(f'拍 {len(beats)} 张；带 loop_header 的 {sum(1 for b in beats if b.loop_header)} 张')
    # 合成/未建模块
    print(f'未建模块 {len(pr.unknown_blocks or [])} 处')
    for rec in (pr.unknown_blocks or [])[:10]:
        print(f'  {Path(rec["file"]).name}:{rec["line"]}  {rec["head"]}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
