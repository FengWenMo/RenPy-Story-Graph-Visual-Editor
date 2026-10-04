#!/usr/bin/env python
"""真实项目里"产物中消失的 4 个 label"到底写了什么？"""
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
LABEL_HEAD = re.compile(r'^(\s*)label\s+([A-Za-z_0-9]+)\s*:')


def block_of(lines: list, idx: int) -> list:
    """取 label 块（含 label 行，到下一个同级或更浅的非空非注释行止）。"""
    m = LABEL_HEAD.match(lines[idx])
    base = len(m.group(1))
    out = [lines[idx]]
    for ln in lines[idx + 1:]:
        if not ln.strip():
            out.append(ln)
            continue
        ind = len(ln) - len(ln.lstrip())
        if ind <= base and not ln.lstrip().startswith('#'):
            break
        out.append(ln)
    return out


def main() -> int:
    files = sorted((PROJECT / 'game').rglob('*.rpy'))
    pr, _g = IncrementalRenPyAnalyzer().analyze(files=files, graph=None)
    beats = beats_from_parse_result(pr)
    out = render_beats_to_rpy(beats)
    missing = sorted(set(pr.labels) - set(LABEL_IN_RPY.findall(out)))

    cache = {}
    for name in missing:
        path, lineno = pr.labels[name]
        print('=' * 68)
        print(f'label {name}   @ {Path(path).name}:{lineno}')
        if path not in cache:
            cache[path] = Path(path).read_text(
                encoding='utf-8', errors='replace').splitlines()
        lines = cache[path]
        for i, ln in enumerate(block_of(lines, lineno - 1), start=lineno):
            print(f'  {i:>4}| {ln}')
        got = [b for b in beats if b.label == name]
        print(f'  → 属于它的 beat 数：{len(got)}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
