# -*- coding: utf-8 -*-
"""临时探针：真机上的「换景」端到端（**只改内存**，不保存、不导出）。

跑法：python tools/_probe_scene_ui_real.py
"""
import os
import tempfile
import time
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')
os.environ['STORYGRAPH_NO_SESSION'] = '1'
os.environ['STORYGRAPH_AUTOSAVE_DIR'] = tempfile.mkdtemp()

from PySide6.QtWidgets import QApplication, QMessageBox            # noqa: E402

from storygraph.pipeline.build import load_asset_registry          # noqa: E402
from storygraph.ui_qt import main_window as MW                     # noqa: E402
from storygraph.ui_qt.main_window import MainWindowQt              # noqa: E402

ROOT = Path(r'd:\千连万理\sr_general')
SCRIPT = ROOT / 'game' / 'chapter' / 'chapter1_1.rpy'


class _Engine:
    def __init__(self, project_root):
        self.project_root = project_root
        self._registry = load_asset_registry(project_root)

    def get_registry(self):
        return self._registry


app = QApplication.instance() or QApplication([])
win = MainWindowQt(_Engine(ROOT), SCRIPT)
win._set_page(win.PAGE_CARD)
win.resize(1200, 800)
win.show()
for _ in range(20):
    app.processEvents()

# 找一个"台上有立绘、本拍没写 scene"的拍，且尺寸面板有行
from storygraph.app.stage import build_frames
frames = build_frames(list(win._session.beats))
target = -1
for i, f in enumerate(frames):
    b = win._session.beats[i]
    if (f.sprites and not str(getattr(b, 'show', '') or '').strip()
        and not str(getattr(b, 'scene', '') or '').strip()):
        target = i
        break
print('target beat', target, 'beats', len(win._session.beats))
win._show_index(target)
for _ in range(10):
    app.processEvents()

panel = win._panel.size_panel()
rows = panel.rows()
print('rows', [(r['kind'], r['tag'], r['spec']) for r in rows])
print('swap visible', panel.slots()[0]['swap'].isVisibleTo(panel) if rows else None)

asked = {}


def _ask(_win, req):
    asked.update(req or {})
    return 'bg bg9_5'


bodies = []


def _confirm(_parent, title, body, *_a, **_k):
    bodies.append((title, body))
    return QMessageBox.StandardButton.Yes


MW._ask_scene_image = _ask
MW.QMessageBox.question = staticmethod(_confirm)

t0 = time.time()
panel.slots()[0]['swap'].click()
for _ in range(10):
    app.processEvents()
dt = time.time() - t0
beat = win._session.beats[target]
print('---- 结果 ----')
print('click ->', f'{dt*1000:.0f} ms')
print('scene =', repr(beat.scene), 'at =', repr(beat.scene_at))
print('op_order =', repr(beat.op_order))
print('asked =', asked.get('spec'), '| at =', asked.get('at'))
if bodies:
    print('---- 确认框 ----')
    print('TITLE:', bodies[-1][0])
    print(bodies[-1][1])
print('---- 控制台 ----')
print(win._console.toPlainText()[-800:])
# 只改内存：不保存、不导出。直接退出（close() 会弹"未导出"模态框）
win.hide()
import sys
sys.stdout.flush()
sys.stderr.flush()
os._exit(0)

