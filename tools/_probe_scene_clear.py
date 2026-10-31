# -*- coding: utf-8 -*-
"""临时探针：真机上"换景会清掉什么"——上一拍台上还有谁/哪些图层。

只读，不改任何文件。给「换景」入口的确认框定口径：
立绘几个、CG/UI 图层几个。
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
    n = 0
    with_sprites = 0
    with_layers = 0
    both = 0
    for i, b in enumerate(beats):
        if not str(getattr(b, 'scene', '') or '').strip():
            continue
        n += 1
        prev = frames[i - 1] if i > 0 else None
        sp = [s.image for s in (prev.sprites if prev else [])]
        ly = [x for x in str(prev.layers if prev else '').split(';') if x.strip()]
        if sp:
            with_sprites += 1
        if ly:
            with_layers += 1
        if sp and ly:
            both += 1
    print(f'{root.name}: beats={len(beats)} scene_beats={n} '
          f'上一拍有立绘={with_sprites} 上一拍有图层={with_layers} 两者都有={both}')
    # 抽前 5 个"换景时台上有人"的例子
    shown = 0
    for i, b in enumerate(beats):
        if not str(getattr(b, 'scene', '') or '').strip() or i == 0:
            continue
        prev = frames[i - 1]
        if not prev.sprites or shown >= 5:
            continue
        shown += 1
        print(f'  beat {i}: scene={b.scene!r} ← 上一拍立绘='
              f'{[s.image for s in prev.sprites]} layers={prev.layers!r}')
