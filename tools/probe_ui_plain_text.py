"""诊断：UI 的「导入纯文字章节」跑完后窗口的图为什么还是 None（临时探针）。"""

import os
import sys
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

import tempfile                                       # noqa: E402

from PySide6.QtWidgets import QApplication, QFileDialog   # noqa: E402

from _ui_project import build_project                  # noqa: E402

td = Path(tempfile.mkdtemp(prefix='uidbg_'))
proj = build_project(td / 'proj')
app = QApplication.instance() or QApplication([])

from storygraph.ui_qt.main_window import MainWindowQt    # noqa: E402


class _Engine:
    def __init__(self, root):
        self.project_root = root
        from storygraph.pipeline.build import load_asset_registry
        self._registry = load_asset_registry(root)

    def get_registry(self):
        return self._registry


win = MainWindowQt(_Engine(proj), proj / 'game' / 'script' / 'ch01.rpy')
txt = proj / 'game' / '9-9纯文字试写.txt'
txt.write_text('1.0 chapter9-9 纯文字试写\n这一句是旁白\n', encoding='utf-8')
QFileDialog.getOpenFileNames = staticmethod(lambda *a, **k: ([str(txt)], ''))
print('调用前 self.graph =', win.graph)
win.import_plain_text()
print('调用后 self.graph =', win.graph)
print('画布里的图     =', getattr(win._canvas, '_graph', None))
print('落盘           =', (proj / 'game' / 'chapter9_9.rpy').is_file())
res = win._session.import_plain_text([txt], None, dry_run=True)
print('dry_run 结果 ok=', res['ok'], 'files=',
      len(getattr(res['plan'], 'files', []) or []))
