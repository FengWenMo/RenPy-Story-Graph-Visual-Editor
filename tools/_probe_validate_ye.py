# -*- coding: utf-8 -*-
"""真机复现 3：validate 对 chapter2_5 的 `base_hand_down` 报什么？"""
import sys
from pathlib import Path

sys.path.insert(0, r'D:\千连万理\Renpy_Story_Graph_Editor')

from storygraph.app.editor import EditorSession          # noqa: E402
from storygraph.pipeline.validate import validate_all    # noqa: E402

ROOT = Path(r'D:\千连万理\sr_general')
CHAPTER = ROOT / 'game' / 'chapter' / 'chapter2_5.rpy'

s = EditorSession(project_root=ROOT)
s.load_assets()
n = s.load(CHAPTER)
reg = s.registry()
print(f'loaded {n} beats; registry={reg is not None}')
try:
    for aid, cand in (reg.images or {}).items():
        if str(cand.get('Tag') or '') == 'ye':
            print('registry row ye:', {k: cand.get(k) for k in (
                'Tag', 'Path', 'Attributes', '_attrs', 'Group', 'Value')})
            break
except Exception as e:  # noqa: BLE001
    print('registry introspect failed:', e)

report = validate_all(s.beats, registry=reg, project_root=str(ROOT))
print(f'\n{len(report.issues)} issues')
for it in report.issues:
    msg = str(getattr(it, 'message', it))
    if 'base_hand_down' in msg or 'base_handdown' in msg or "'ye'" in msg:
        print(f'  {getattr(it, "level", "?")}/{getattr(it, "code", "?")} '
              f'{getattr(it, "severity", "?")} @{getattr(it, "where", "?")}: {msg}')
