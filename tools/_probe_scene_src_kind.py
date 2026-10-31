# -*- coding: utf-8 -*-
"""临时探针：真机里"背景的出处"那一拍写的是 scene 还是 show bg？"""
from pathlib import Path

from storygraph.app.stage import build_frames
from storygraph.parsing.discover import discover_scripts
from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
from storygraph.story.atl import scan_transforms
from storygraph.story.beat_build import beats_from_statements

ROOT = Path(r'd:\千连万理\sr_general')
an = IncrementalRenPyAnalyzer()
pr, _g = an.analyze(files=discover_scripts(ROOT), graph=None)
beats = beats_from_statements(pr.statements)
index = scan_transforms(ROOT)
frames = build_frames(beats, transform_index=index, screen=(1920, 1080))

from_scene = from_show = unknown = 0
show_examples = []
for f in frames:
    if not f.scene:
        continue
    src = getattr(f, 'scene_src', -1)
    if src is None or src < 0:
        unknown += 1
        continue
    b = beats[src]
    if str(getattr(b, 'scene', '') or '').strip():
        from_scene += 1
    else:
        show = str(getattr(b, 'show', '') or '')
        if show.split()[0:1] == ['bg'] or 'bg ' in show:
            from_show += 1
            if len(show_examples) < 6:
                show_examples.append((src, show, f.scene, f.scene_at))
        else:
            unknown += 1
print('frames with scene:', sum(1 for f in frames if f.scene))
print('source wrote scene:', from_scene, '| wrote show bg:', from_show,
      '| unknown:', unknown)
for e in show_examples:
    print('  ', e)
