# -*- coding: utf-8 -*-
"""Read-only probe: where does a beat's src range start/end, and where is the show line?"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding='utf-8')

from storygraph.parsing.renpy_parser import ParseResultBuilder, RenPyParser
from storygraph.story.beat_build import beats_from_parse_result
from storygraph.story.source_style import read_text

ROOT = Path(r'D:\千连万理\sr_general')


def load(path):
    pr = ParseResultBuilder().build(RenPyParser().parse(str(path)))
    return beats_from_parse_result(pr)


def show(path, limit=12):
    text, _ = read_text(path)
    lines = text.split('\n')
    beats = load(path)
    print(f'=== {path.name}  beats={len(beats)} ===')
    shown = 0
    for b in beats:
        if not str(b.show or '').strip():
            continue
        print(f'-- seq={b.seq} L{b.src_first}-{b.src_last} show={b.show!r} '
              f'text={str(b.text or "")[:20]!r}')
        for n in range(b.src_first, min(b.src_last, b.src_first + 7) + 1):
            print(f'   {n:4}: {lines[n - 1]!r}')
        shown += 1
        if shown >= limit:
            break


for name in ('chapter2_1.rpy', 'chapter1_2.rpy'):
    show(ROOT / 'game' / 'chapter' / name)
