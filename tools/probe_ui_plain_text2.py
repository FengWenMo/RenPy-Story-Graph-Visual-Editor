"""诊断 2：直接看 `_session.import_chapters` 在 UI 项目里返回什么（临时探针）。"""

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

from PySide6.QtWidgets import QApplication                 # noqa: E402
from _ui_project import build_project                      # noqa: E402
from storygraph.pipeline.build import load_asset_registry  # noqa: E402

td = Path(tempfile.mkdtemp(prefix='uidbg2_'))
proj = build_project(td / 'proj')
app = QApplication.instance() or QApplication([])

from storygraph.app.editor import EditorSession            # noqa: E402

s = EditorSession(project_root=proj)
print('registry =', type(s.registry()).__name__)
txt = proj / 'game' / '9-9试写.txt'
txt.write_text('1.0 chapter9-9 试写\n这一句是旁白\n', encoding='utf-8')
r = s.import_plain_text([txt], None)
print('ok           =', r['ok'])
print('written      =', r['written'])
print('needs_decision=', r['needs_decision'])
print('messages     =', r['messages'])
inner = r['import_result']
print('import_result=', None if inner is None else
      {k: v for k, v in inner.items() if k != 'graph'})
print('graph nodes  =', None if inner is None else len(inner['graph'].nodes))
