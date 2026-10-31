# -*- coding: utf-8 -*-
"""
临时探针：卡片「尺寸」一格 + **本景单独改写提示**的截图（offscreen）。

造一景里背景被单独改了 3 次（`notice`，琥珀色）、CG 改了 1 次（`hint`，灰字），
看右栏窄宽度下会不会挤、会不会把面板顶宽。
"""
import os
import tempfile
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')
os.environ['STORYGRAPH_NO_SESSION'] = '1'
os.environ['STORYGRAPH_AUTOSAVE_DIR'] = tempfile.mkdtemp()

from PIL import Image                                        # noqa: E402
from PySide6.QtWidgets import QApplication                   # noqa: E402

SCRIPT = '''define e = Character("爱莲", color="#c8ffc8", image="ling")

transform bgfit:
    fit "cover"

transform half:
    zoom 0.5

label ch01:
    scene bg bg7_0 at bgfit
    show cg cg01 at half
    e "开场。背景是铺满的，CG 是 50%。"
    show bg bg7_0 at half
    e "这一拍背景被单独改成了 50%。"
    show bg bg7_0 at half
    e "又一次。"
    show bg bg7_0 at half
    e "第三次 —— 这一景里已经有 3 处单独改写了。"
'''

GUI = '''init python:
    gui.init(1920, 1080)
'''

root = Path(tempfile.mkdtemp()) / 'proj'
game = root / 'game'
(game / 'script').mkdir(parents=True)
(game / 'images' / 'bg').mkdir(parents=True)
(game / 'images' / 'cg').mkdir(parents=True)
(game / 'gui.rpy').write_text(GUI, encoding='utf-8')
(game / 'script' / 'ch01.rpy').write_text(SCRIPT, encoding='utf-8')
Image.new('RGB', (3840, 2160), (40, 90, 140)).save(
    game / 'images' / 'bg' / 'bg7_0.png')
Image.new('RGB', (1600, 900), (200, 120, 40)).save(
    game / 'images' / 'cg' / 'cg01.png')

from storygraph.ui_qt.main_window import MainWindowQt        # noqa: E402
from storygraph.pipeline.build import load_asset_registry    # noqa: E402


class _Engine:
    def __init__(self, project_root):
        self.project_root = project_root
        self._registry = load_asset_registry(project_root)

    def get_registry(self):
        return self._registry


app = QApplication.instance() or QApplication([])
win = MainWindowQt(_Engine(root), game / 'script' / 'ch01.rpy')
win._set_page(win.PAGE_CARD)
win.resize(1200, 800)
win.show()
for _ in range(20):
    app.processEvents()
win._show_index(0)
for _ in range(30):
    app.processEvents()

out = Path('tools/_advice_panel.png')
panel = win._panel
panel.grab().save(str(out))
size = win._panel.size_panel()
print('beats', len(win._session.beats))
for row in size.rows():
    advice = row.get('advice')
    print('row', row['kind'], row['tag'], '| level', 
          getattr(advice, 'level', None), '| count', getattr(advice, 'count', None))
    if advice is not None:
        print('   short :', advice.short(label=row['label']))
        print('   tooltip:', advice.text(label=row['label']).replace('\n', ' / '))
for i, slot in enumerate(size.slots()):
    if slot['row'] is None:
        continue
    lab = slot['advice']
    print(f'slot {i}: visible={lab.isVisibleTo(size)} role={lab.property("role")} '
          f'text={lab.text()!r}')
print('min width', size.minimumSizeHint().width())
win.hide()
