#!/usr/bin/env python
"""
探针：真机里那个**游戏主循环**现在渲染成了什么（作用域那一步的验收）。

只看一件事：`while current_round < game_rounds:` 那一块，
原文与产物**逐行摆在一起**（块体有没有少吞/多吞、缩进对不对）。

    python tools/probe_loop_scope_real.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
from storygraph.story.beat_build import beats_from_parse_result
from storygraph.story.beat_render import render_beats_to_rpy

REAL = Path(r'D:\千连万理\RenPyTest\sr_general')
TARGET = 'while'


def _region(text: str, needle: str, pad: int = 6) -> list:
    lines = text.split('\n')
    for i, ln in enumerate(lines):
        if ln.strip().startswith(needle):
            return lines[max(0, i - pad):i + pad]
    return []


def main() -> int:
    src = REAL / 'game' / 'minigame' / 'mini_game1.rpy'
    if not src.is_file():
        print(f'找不到：{src}')
        return 2
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[src], graph=None)
    beats = beats_from_parse_result(pr)
    out = render_beats_to_rpy(beats)

    print(f'源：{src}')
    print(f'语句 {len(pr.statements)} 条 · 拍 {len(beats)} 张')
    loops = [s for s in pr.statements if s.kind == 'loop']
    print(f'循环语句 {len(loops)} 条：', [s.payload.get('header') for s in loops])
    body = [s for s in pr.statements
            if str(getattr(s, 'nest_path', '') or '').startswith('loop:')]
    print(f'块体语句 {len(body)} 条：',
          [(s.kind, str(getattr(s, 'nest_path', ''))) for s in body])
    print()
    print('--- 原文（含上下文）---')
    for ln in _region(src.read_text(encoding='utf-8'), TARGET):
        print(f'  |{ln}')
    print('--- 产物（含上下文）---')
    for ln in _region(out, TARGET):
        print(f'  |{ln}')
    print()
    src_lines = [ln for ln in _region(src.read_text(encoding='utf-8'), TARGET)
                 if ln.strip()]
    out_lines = [ln for ln in _region(out, TARGET) if ln.strip()]
    missing = [ln for ln in src_lines if ln not in out_lines]
    print('原文里对不上的行：', missing or '（无）')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
