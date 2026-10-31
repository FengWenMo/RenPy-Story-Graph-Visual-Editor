# -*- coding: utf-8 -*-
"""临时探针：真机上 size_rows 的耗时与输出形状。"""
import time
from pathlib import Path

from storygraph.app.stage import build_frames
from storygraph.app.stage_render import size_rows
from storygraph.parsing.discover import discover_scripts
from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
from storygraph.story.atl import scan_transforms
from storygraph.story.beat_build import beats_from_statements

ROOT = Path(r'd:\千连万理\sr_general')
an = IncrementalRenPyAnalyzer()
pr, _graph = an.analyze(files=discover_scripts(ROOT), graph=None)
beats = beats_from_statements(pr.statements)
index = scan_transforms(ROOT)
screen = (1920, 1080)
frames = build_frames(beats, transform_index=index, screen=screen)
print('beats', len(beats), 'frames', len(frames), 'transforms', len(index))

sample = [i for i, f in enumerate(frames) if f.layers][:3]
for i in sample:
    print('=== frame', i, 'scene=', frames[i].scene, frames[i].scene_at,
          'layers=', frames[i].layers)
    for row in size_rows(frames[i], project_root=ROOT, screen=screen,
                         transform_index=index):
        print('  row', row['kind'], row['tag'], '|', row['note'])
        for it in row['items'][:5]:
            print('     ', it['ok'], it['current'], it['text'], '|', it['why'])

t0 = time.time()
n = 0
for f in frames[600:700]:
    n += len(size_rows(f, project_root=ROOT, screen=screen,
                       transform_index=index))
dt = time.time() - t0
print(f'100 frames: {dt*1000:.0f} ms, {n} rows -> {dt*10:.2f} ms/frame')
