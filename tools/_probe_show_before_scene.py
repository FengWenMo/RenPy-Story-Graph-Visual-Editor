# -*- coding: utf-8 -*-
"""临时探针：真机里「同一拍 show 在 scene 之前」有多少（影响换景清台口径）。"""
from pathlib import Path

from storygraph.parsing.discover import discover_scripts
from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
from storygraph.story.beat_build import beats_from_statements

for root in (Path(r'd:\千连万理\sr_general'),
             Path(r'd:\千连万理\renpytest\sr_general')):
    an = IncrementalRenPyAnalyzer()
    pr, _graph = an.analyze(files=discover_scripts(root), graph=None)
    beats = beats_from_statements(pr.statements)
    n = 0
    for i, b in enumerate(beats):
        if not str(getattr(b, 'scene', '') or '').strip():
            continue
        toks = [t for t in (b.op_order or '').split(',') if t.strip()]
        verbs = [t.split('#')[0] for t in toks]
        if 'scene' in verbs and 'show' in verbs:
            if verbs.index('show') < verbs.index('scene'):
                n += 1
                print(f'  {root.name} beat {i}: op_order={b.op_order!r} '
                      f'show={b.show!r} scene={b.scene!r}')
    print(f'{root.name}: show 排在 scene 之前的拍 = {n}')
