# -*- coding: utf-8 -*-
"""真机统计：每章的立绘 show 有多少拍带 `$ renpy.transition(...)`。只读。"""
import sys
from pathlib import Path

sys.path.insert(0, r'D:\千连万理\Renpy_Story_Graph_Editor')

from storygraph.app.editor import EditorSession                  # noqa: E402
from storygraph.assets.registry_load import load_asset_registry  # noqa: E402
from storygraph.story.transition_expr import (                   # noqa: E402
    SOURCE_RENPY, beat_transitions)

ROOT = Path(r'D:\千连万理\sr_general')
CHAPTERS = sorted((ROOT / 'game' / 'chapter').glob('*.rpy'))
REG = load_asset_registry(ROOT)


def actor_tags(session):
    sym = session.symbols
    return sym


def is_actor(tag, sym):
    try:
        return bool(sym.is_actor_tag(tag))
    except Exception:
        return tag in {'lan', 'ling', 'ye', 'xing', 'qing'}


total_show = total_with = 0
for chapter in CHAPTERS:
    session = EditorSession(project_root=ROOT)
    session._registry_provider = lambda: REG
    session.load(chapter)
    sym = session.refresh_symbols()
    n_show = n_with = 0
    gaps = []
    extra = []
    for i, beat in enumerate(session.beats):
        specs = [s.strip() for s in str(beat.show or '').split(';') if s.strip()]
        actor_specs = [s for s in specs
                       if is_actor(s.split()[0].split('@')[0], sym)]
        if not actor_specs:
            continue
        n_show += 1
        calls = [c for c in beat_transitions(beat)
                 if getattr(c, 'source', '') == SOURCE_RENPY]
        if calls:
            n_with += 1
        else:
            gaps.append((beat.seq, beat.src_first, '; '.join(actor_specs),
                         str(beat.text or '')[:18]))
        if len(actor_specs) > 1:
            extra.append((beat.seq, '; '.join(actor_specs)))
    total_show += n_show
    total_with += n_with
    print(f'{chapter.name:22} 立绘拍 {n_show:4}  带转场 {n_with:4}  缺 {len(gaps):4}')
    for seq, line, spec, text in gaps[:6]:
        print(f'    · seq={seq} L{line}: {spec}  | {text}')
    if len(gaps) > 6:
        print(f'    … 还有 {len(gaps) - 6} 条')
    if extra:
        print(f'    （一拍里多条立绘 show：{len(extra)} 拍，例 {extra[:2]}）')

print(f'\nTOTAL 立绘拍 {total_show}  带转场 {total_with}  '
      f'缺 {total_show - total_with}')
