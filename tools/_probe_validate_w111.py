# -*- coding: utf-8 -*-
"""真机复现 3b：validate 的 W111 全量（不只 ye）。"""
import sys
from pathlib import Path

sys.path.insert(0, r'D:\千连万理\Renpy_Story_Graph_Editor')

from storygraph.app.editor import EditorSession          # noqa: E402
from storygraph.pipeline.validate import validate_all    # noqa: E402

ROOT = Path(r'D:\千连万理\sr_general')
CHAPTER = ROOT / 'game' / 'chapter' / 'chapter2_5.rpy'

s = EditorSession(project_root=ROOT)
s.load_assets()
s.load(CHAPTER)
reg = s.registry()
report = validate_all(s.beats, registry=reg, project_root=str(ROOT),
                      dedupe=False, cap_per_code=0)
w111 = [it for it in report.issues if getattr(it, 'code', '') == 'W111']
print(f'total={len(report.issues)} W111={len(w111)}')
for it in w111:
    print(f'  {it.where}: {it.message}')
