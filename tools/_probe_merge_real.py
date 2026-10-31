# -*- coding: utf-8 -*-
"""
临时探针：**合并回源**（`story.at_merge`）在真机上到底能合多少、哪些合不动。

**只读**：只 build 帧 + `plan_at_merge`（它连 Beat 都不改），不写盘。

    $env:PYTHONIOENCODING='utf-8'; $env:PYTHONPATH='.'
    python tools/_probe_merge_real.py            # 两个项目对照
"""
import sys
from collections import Counter
from pathlib import Path

from storygraph.app.stage import build_frames
from storygraph.app.stage_render import size_rows
from storygraph.parsing.discover import discover_scripts
from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
from storygraph.story.at_merge import plan_at_merge
from storygraph.story.atl import scan_transforms
from storygraph.story.beat_build import beats_from_statements

ROOTS = [r'd:\千连万理\sr_general', r'd:\千连万理\renpytest\sr_general']
if len(sys.argv) > 1:
    ROOTS = sys.argv[1:]

for root in ROOTS:
    ROOT = Path(root)
    print('=' * 70)
    print('项目：', ROOT)
    an = IncrementalRenPyAnalyzer()
    pr, _graph = an.analyze(files=discover_scripts(ROOT), graph=None)
    beats = beats_from_statements(pr.statements)
    index = scan_transforms(ROOT)
    screen = (1920, 1080)
    frames = build_frames(beats, transform_index=index, screen=screen)
    print('beats', len(beats), 'frames', len(frames))

    rows_with = 0
    ok_plans = 0
    steps = 0
    skips = 0
    reasons = Counter()
    samples = []
    seen_sources = set()
    for i, frame in enumerate(frames):
        for row in size_rows(frame, project_root=ROOT, screen=screen,
                             transform_index=index, beats=beats):
            advice = row.get('advice')
            if advice is None or advice.unknown or not advice.count:
                continue
            rows_with += 1
            key = (row['source'], row['kind'], row['tag'])
            plan = plan_at_merge(beats, row['source'], kind=row['kind'],
                                 tag=row['tag'], label=row['label'])
            if plan.ok:
                ok_plans += 1
                steps += plan.count
                skips += plan.skipped_count
                for skip in plan.skipped:
                    reasons[skip.reason[:18]] += 1
                if key not in seen_sources and len(samples) < 3:
                    seen_sources.add(key)
                    samples.append((i, plan.diff_text().splitlines()[:6]))
            else:
                reasons['计划拒绝：' + (plan.reason or '')[:16]] += 1
    print(f'有单独改写的行：{rows_with} 处（同一源会重复计，每帧一行）')
    print(f'能出计划的：{ok_plans}；真要改的：{steps} 处；改不了：{skips} 处')
    for reason, n in reasons.most_common(6):
        print(f'   · {n:4d}  {reason}')
    for i, head in samples:
        print(f'   —— 第 {i + 1} 拍的例子 ——')
        for line in head:
            print('      ' + line)
