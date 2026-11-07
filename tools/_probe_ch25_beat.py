# -*- coding: utf-8 -*-
"""真机复现 7：chapter2_5 第 183 行那一拍的卡片字段。"""
import sys
from pathlib import Path

sys.path.insert(0, r'D:\千连万理\Renpy_Story_Graph_Editor')

from storygraph.app.editor import EditorSession  # noqa: E402

ROOT = Path(r'D:\千连万理\sr_general')
CHAPTER = ROOT / 'game' / 'chapter' / 'chapter2_5.rpy'

s = EditorSession(project_root=ROOT)
s.load(CHAPTER)
found = []
for i, b in enumerate(s.beats):
    if 'base_hand_down' in str(b.show or ''):
        found.append(i)
print('beats with base_hand_down:', found)
for i in found:
    b = s.beats[i]
    print(f'\n--- beat {i} src={b.src_first}-{b.src_last} show={b.show!r}')
    print('  beat at =', repr(getattr(b, 'at', None)),
          '| position =', repr(getattr(b, 'position', None)))
    st = s.stage_state(i)
    print('  stage actors:', [(a['spec'], a['at']) for a in st.get('actors') or []])
    for c in s.cards(i):
        if c.get('tag') == 'ye':
            print('  ye card:', {k: c.get(k) for k in (
                'tag', 'image', 'expression', 'position', 'at',
                'inherited', 'effective_at', 'inherited_at')})
    print('  fields at/position:', s.fields(i).get('at'),
          '/', s.fields(i).get('position'))
