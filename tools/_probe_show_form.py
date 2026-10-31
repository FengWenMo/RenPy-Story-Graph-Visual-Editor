# -*- coding: utf-8 -*-
"""临时探针：真机 beat.show 里 CG 的 at 是 @ 还是 at 关键字？"""
from pathlib import Path

from storygraph.parsing.discover import discover_scripts
from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
from storygraph.story.beat_build import beats_from_statements

ROOT = Path(r'd:\千连万理\sr_general')
files = discover_scripts(ROOT)
an = IncrementalRenPyAnalyzer()
pr, graph = an.analyze(files=files, graph=None)
beats = beats_from_statements(pr.statements)
n = 0
with_layers = 0
for i, b in enumerate(beats):
    show = str(getattr(b, 'show', '') or '')
    la = getattr(b, 'layers_at', None)
    if la:
        with_layers += 1
        if with_layers <= 8:
            print('LAYERS', i, repr(show), '|', repr(getattr(b, 'layers', '')),
                  '|', la)
    if 'at ' in show and ('cg' in show or 'ui' in show):
        n += 1
        if n <= 5:
            print('AT-KW', i, repr(show), '| layers=', repr(getattr(b, 'layers', '')),
                  '| layers_at=', la)
print('total beats', len(beats), 'with layers_at', with_layers,
      'with " at " in show', n)
cg = [i for i, b in enumerate(beats) if 'cg' in str(getattr(b, 'show', '') or '')]
print('beats with cg in show:', len(cg), cg[:10])
for i in cg[:10]:
    b = beats[i]
    print('  ', i, repr(getattr(b, 'show', '')), '| scene=', repr(b.scene),
          '| layers=', repr(getattr(b, 'layers', '')),
          '| kind=', getattr(b, 'kind', ''))

