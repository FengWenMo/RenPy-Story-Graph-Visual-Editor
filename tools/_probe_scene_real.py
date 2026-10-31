# -*- coding: utf-8 -*-
"""临时探针：真机上「换景」计划能不能算、算得对不对、算得快不快。

只读（`plan_scene_change` 只算，不动 Beat、不写盘）。
"""
import time
from pathlib import Path

from storygraph.app.stage import build_frames, image_index, plan_scene_change
from storygraph.parsing.discover import discover_scripts
from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
from storygraph.story.atl import scan_transforms
from storygraph.story.beat_build import beats_from_statements
from storygraph.story.stage_snapshot import stage_snapshots

ROOT = Path(r'd:\千连万理\sr_general')
an = IncrementalRenPyAnalyzer()
pr, _graph = an.analyze(files=discover_scripts(ROOT), graph=None)
beats = beats_from_statements(pr.statements)
index = scan_transforms(ROOT)
imgs = image_index(ROOT)
print('beats', len(beats), 'transforms', len(index), 'images', len(imgs))

# 用项目里真实存在的两张背景做"换到 X"
cands = [k for k in imgs if k.startswith('bg')][:4]
print('候选图名', cands)

frames = build_frames(beats, transform_index=index)
# 找几个"台上有立绘、本拍没写 scene"的拍
targets = []
for i, b in enumerate(beats):
    if str(getattr(b, 'scene', '') or '').strip():
        continue
    if frames[i].sprites:
        targets.append(i)
    if len(targets) >= 5:
        break

rows = stage_snapshots(beats)
t0 = time.time()
for i in targets:
    b = beats[i]
    prev = rows[i - 1]['state'] if i > 0 else {}
    edit = plan_scene_change(beats, i, 'bg bg9_5', at='bgfit',
                             project_root=ROOT, transform_index=index)
    print(f'beat {i}: ok={edit.ok} reason={edit.reason!r}')
    print(f'   清台={edit.cleared_sprites} 图层={edit.cleared_layers}'
          f' | 快照说上一拍={[a[0] for a in prev.get("actors") or []]}')
    print(f'   op_order {edit.order_before!r} -> {edit.order_after!r}')
    print(f'   rpy: {edit.rpy_before.splitlines()[-3:]} ->'
          f' {edit.rpy_after.splitlines()[-3:]}')
dt = time.time() - t0
print(f'{len(targets)} 个计划共 {dt*1000:.0f} ms -> {dt/max(1,len(targets))*1000:.1f} ms/个')
