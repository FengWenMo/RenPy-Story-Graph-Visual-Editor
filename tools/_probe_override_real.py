# -*- coding: utf-8 -*-
"""
临时探针：真机上"本景还有几处单独改写"（`override_advice`）到底出多少、代价多少。

**只读**：只 build 帧 + 调 `size_rows`，不写盘、不改 Beat。

    $env:PYTHONIOENCODING='utf-8'; $env:PYTHONPATH='.'
    python tools/_probe_override_real.py
"""
import sys
import time
from collections import Counter
from pathlib import Path

from storygraph.app.stage import build_frames
from storygraph.app.stage_render import size_rows
from storygraph.parsing.discover import discover_scripts
from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
from storygraph.story.atl import scan_transforms
from storygraph.story.beat_build import beats_from_statements
from storygraph.story.override_advice import override_map

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else r'd:\千连万理\sr_general')
print('项目：', ROOT)
an = IncrementalRenPyAnalyzer()
pr, _graph = an.analyze(files=discover_scripts(ROOT), graph=None)
beats = beats_from_statements(pr.statements)
index = scan_transforms(ROOT)
screen = (1920, 1080)
frames = build_frames(beats, transform_index=index, screen=screen)
print('beats', len(beats), 'frames', len(frames), 'transforms', len(index))

# ① 一次扫描本身多贵（全章 200 拍量级）
t0 = time.perf_counter()
for i in range(0, len(beats), 10):
    override_map(beats, i)
dt = time.perf_counter() - t0
print(f'override_map：{len(beats)//10} 次 {dt*1000:.0f} ms'
      f'（{dt/max(1, len(beats)//10)*1000:.2f} ms/次）')

# ② 逐拍：档位分布 + 最常见的形状
t0 = time.perf_counter()
levels = Counter()
by_label = Counter()
worst = []
rows_total = 0
for i, frame in enumerate(frames):
    rows = size_rows(frame, project_root=ROOT, screen=screen,
                     transform_index=index, beats=beats)
    rows_total += len(rows)
    for row in rows:
        advice = row.get('advice')
        if advice is None:
            continue
        levels[advice.level] += 1
        if advice.count:
            by_label[row['label']] += 1
            worst.append((advice.count, i, row['label'], advice.beats[:6],
                          advice.short(label=row['label'])))
dt = time.perf_counter() - t0
print(f'size_rows（含 advice）：{len(frames)} 拍 / {rows_total} 行'
      f' → {dt*1000:.0f} ms（{dt/max(1, len(frames))*1000:.2f} ms/拍）')
print('档位分布：', dict(levels))
print('有提示的行（按类型）：', dict(by_label))
worst.sort(reverse=True)
print(f'\n处数最多的 10 行（共 {len(worst)} 行有提示）：')
for count, i, label, bs, short in worst[:10]:
    print(f'  第 {i+1} 拍 {label}：{count} 处 {bs}')
    print(f'      {short}')
