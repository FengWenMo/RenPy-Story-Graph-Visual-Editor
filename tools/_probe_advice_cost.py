# -*- coding: utf-8 -*-
"""临时探针：`size_rows` 加上 advice 之后的**边际**代价（只读）。"""
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
pr, _ = an.analyze(files=discover_scripts(ROOT), graph=None)
beats = beats_from_statements(pr.statements)
index = scan_transforms(ROOT)
screen = (1920, 1080)
frames = build_frames(beats, transform_index=index, screen=screen)
sample = frames[600:800]
for kw in ({}, {'beats': beats}):
    t0 = time.perf_counter()
    for f in sample:
        size_rows(f, project_root=ROOT, screen=screen,
                  transform_index=index, **kw)
    dt = time.perf_counter() - t0
    print(list(kw) or ['no-beats'], f'{dt / len(sample) * 1000:.2f} ms/frame')
