# -*- coding: utf-8 -*-
"""真机统计：立绘拍里各种边界情况各有多少（决定批处理规则）。只读。"""
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, r'D:\千连万理\Renpy_Story_Graph_Editor')

from storygraph.app.editor import EditorSession                  # noqa: E402
from storygraph.assets.registry_load import load_asset_registry  # noqa: E402
from storygraph.story.transition_expr import (                   # noqa: E402
    SOURCE_INLINE_WITH, SOURCE_RENPY, SOURCE_WITH, beat_transitions)

ROOT = Path(r'D:\千连万理\sr_general')
CHAPTERS = sorted((ROOT / 'game' / 'chapter').glob('*.rpy'))
REG = load_asset_registry(ROOT)
ACTORS = {'lan', 'ling', 'ye', 'xing', 'qing'}

c = Counter()
per_chapter = []
for chapter in CHAPTERS:
    s = EditorSession(project_root=ROOT)
    s._registry_provider = lambda: REG
    s.load(chapter)
    s.refresh_symbols()
    n = Counter()
    for b in s.beats:
        specs = [x.strip() for x in str(b.show or '').split(';') if x.strip()]
        actor = [x for x in specs if x.split()[0].split('@')[0] in ACTORS]
        if not actor:
            continue
        n['sprite_beats'] += 1
        trans = beat_transitions(b)
        has_renpy = any(x.source == SOURCE_RENPY for x in trans)
        has_with = any(x.source in (SOURCE_WITH, SOURCE_INLINE_WITH)
                       for x in trans)
        if has_renpy:
            n['has_renpy'] += 1
        if has_with:
            n['has_with'] += 1
        if not trans:
            n['no_transition'] += 1
        if b.code.strip() and not has_renpy:
            n['other_code'] += 1
        if len(actor) > 1:
            n['multi_show'] += 1
        if b.hide.strip():
            n['with_hide'] += 1
        if b.condition.strip():
            n['conditional'] += 1
        if any(x.rstrip().endswith(':') for x in actor):
            n['atl_header'] += 1
        if b.kind == 'menu':
            n['menu'] += 1
    per_chapter.append((chapter.name, n))
    c.update(n)

for name, n in per_chapter:
    if n['sprite_beats']:
        print(f'{name:22} beats={n["sprite_beats"]:4} '
              f'renpy={n["has_renpy"]:4} with={n["has_with"]:3} '
              f'none={n["no_transition"]:4} other_code={n["other_code"]:3} '
              f'multi={n["multi_show"]:2} hide={n["with_hide"]:3} '
              f'cond={n["conditional"]:3} atl={n["atl_header"]:2} '
              f'menu={n["menu"]:2}')
print('\nTOTAL:', dict(c))
