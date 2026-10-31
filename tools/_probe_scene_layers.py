# -*- coding: utf-8 -*-
"""临时探针：`scene` 之后快照里还留着的 CG/UI 图层 —— 这些是"看到的 ≠ 引擎"。

如果某个图层在被 scene 清台之后**仍然**出现在下一帧，那么：
· 引擎里它已经没了（scene 清整个 master 层）；
· 预览/卡片还会画它 → 工具在骗人。
只读探针，不改任何文件。
"""
from pathlib import Path

from storygraph.app.stage import build_frames
from storygraph.parsing.discover import discover_scripts
from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
from storygraph.story.beat_build import beats_from_statements

for root in (Path(r'd:\千连万理\sr_general'),
             Path(r'd:\千连万理\renpytest\sr_general')):
    an = IncrementalRenPyAnalyzer()
    pr, _graph = an.analyze(files=discover_scripts(root), graph=None)
    beats = beats_from_statements(pr.statements)
    frames = build_frames(beats)
    n = lie = 0
    for i, b in enumerate(beats):
        if not str(getattr(b, 'scene', '') or '').strip() or i == 0:
            continue
        n += 1
        prev = frames[i - 1]
        now = frames[i]
        prev_layers = [x.strip() for x in str(prev.layers).split(';')
                       if x.strip() and x.strip() != prev.scene]
        now_layers = [x.strip() for x in str(now.layers).split(';')
                      if x.strip() and x.strip() != now.scene]
        if prev_layers and now_layers:
            lie += 1
            print(f'{root.name} beat {i}: scene={b.scene!r} '
                  f'清台前图层={prev_layers} 清台后仍在={now_layers} '
                  f'本拍 hide={b.hide!r}')
    print(f'{root.name}: scene 拍 {n}，清台后图层仍在 {lie}')
