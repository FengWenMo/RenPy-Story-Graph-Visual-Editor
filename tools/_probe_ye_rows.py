# -*- coding: utf-8 -*-
"""真机复现 4：资源表里 Tag=ye 的行 / `_attrs` 是怎么来的。"""
import sys
from pathlib import Path

sys.path.insert(0, r'D:\千连万理\Renpy_Story_Graph_Editor')

from storygraph.app.editor import EditorSession  # noqa: E402

ROOT = Path(r'D:\千连万理\sr_general')
s = EditorSession(project_root=ROOT)
s.load_assets()
reg = s.registry()
rows = []
for aid, cand in (reg.images or {}).items():
    if str(cand.get('Tag') or '') == 'ye':
        rows.append((aid, cand))
print(f'{len(rows)} rows with Tag=ye')
for aid, cand in rows[:12]:
    print(aid, '|', cand.get('Path'), '| attrs=', cand.get('_attrs'),
          '| Attributes=', cand.get('Attributes'), '| Group=', cand.get('Group'),
          '| Value=', cand.get('Value'))
