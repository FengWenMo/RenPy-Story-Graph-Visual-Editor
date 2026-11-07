# -*- coding: utf-8 -*-
"""真机复现 5：chapter3_1_1 的“沿用”卡（带跨章入口状态）。"""
import sys
from pathlib import Path

sys.path.insert(0, r'D:\千连万理\Renpy_Story_Graph_Editor')

from storygraph.app.editor import EditorSession          # noqa: E402
from storygraph.app import stage_entry as SE             # noqa: E402

ROOT = Path(r'D:\千连万理\sr_general')
CHAPTER = ROOT / 'game' / 'chapter' / 'chapter3_1_1.rpy'

sig, report = SE.analyze(ROOT)
entry = SE.entry_for(ROOT, str(CHAPTER))
print('report root:', getattr(report, 'root', None), '| entry:', entry)
if entry is not None:
    print('entry note:', entry.describe())
    print('entry state actors:', (entry.state or {}).get('actors'))

s = EditorSession(project_root=ROOT)
s.load(CHAPTER)
if entry is not None:
    s.set_stage_entry(entry)

for i in range(min(len(s.beats), 8)):
    b = s.beats[i]
    print(f'\n--- beat {i} src={getattr(b, "src_first", None)}-'
          f'{getattr(b, "src_last", None)} show={b.show!r}')
    st = s.stage_state(i)
    print('  stage actors:', [(a['spec'], a['at']) for a in st.get('actors') or []])
    for c in s.cards(i):
        print('  card:', {k: c.get(k) for k in (
            'tag', 'image', 'expression', 'position', 'at',
            'inherited', 'effective_at', 'inherited_at')})
