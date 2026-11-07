# -*- coding: utf-8 -*-
"""真机复现 8：chapter2_5 里有多少拍带着 `ye base_hand_down gratified` 卡。"""
import sys
from pathlib import Path

sys.path.insert(0, r'D:\千连万理\Renpy_Story_Graph_Editor')

from storygraph.app.editor import EditorSession  # noqa: E402

ROOT = Path(r'D:\千连万理\sr_general')
CHAPTER = ROOT / 'game' / 'chapter' / 'chapter2_5.rpy'

s = EditorSession(project_root=ROOT)
s.load(CHAPTER)
own, inherited = [], []
for i, b in enumerate(s.beats):
    for c in s.cards(i):
        if str(c.get('image') or '') == 'ye base_hand_down gratified':
            (inherited if c.get('inherited') else own).append(
                (i, getattr(b, 'src_first', None), c.get('at'),
                 c.get('inherited_at')))
print(f'own={len(own)} inherited={len(inherited)}')
print('own (beat, src_line, at, inherited_at):', own)
print('inherited first/last:', inherited[:3], '...', inherited[-3:])
