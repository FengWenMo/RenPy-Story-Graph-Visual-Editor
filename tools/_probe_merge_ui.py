# -*- coding: utf-8 -*-
"""
临时探针：**合并回源**的两张截图（offscreen）。

  1. `tools/_merge_panel.png` —— 卡片「尺寸」一格：提示行下面多出
     「一并改成一样（2 处）」按钮（右栏窄，看会不会挤、会不会顶宽）；
  2. `tools/_merge_dialog.png` —— 确认框：一处一个勾（默认全勾）+ 下面
     单独列出改不了的那几处。

造一景：背景 `bgfit`，后面两次 `show … at half`；CG 那处写成复杂子句
（`behind ye`）—— 它会进 skipped，正好看"改不了也要说出来"长什么样。
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
    show cg cg01@half
    e "开场。背景是铺满的，CG 是 50%。"
    show bg bg7_0@half
    e "这一拍背景被单独改成了 50%。"
    show cg cg02 behind ye
    e "CG 换成第二张，还带了 behind —— 这一处工具不猜。"
    show bg bg7_0 with dissolve
    e "这条是 `show bg … with dissolve`（真机最常见的那种写法）—— 复杂子句，"
    e "合并会把它列进"改不了"那一栏。"
    show bg bg7_0@half
    e "背景又被单独改了一次。"
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
Image.new('RGB', (1600, 900), (120, 200, 90)).save(
    game / 'images' / 'cg' / 'cg02.png')

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

out = Path('tools/_merge_panel.png')
win._panel.grab().save(str(out))

size = win._panel.size_panel()
print('beats', len(win._session.beats))
for i, slot in enumerate(size.slots()):
    if slot['row'] is None:
        continue
    row = slot['row']
    btn = slot['merge']
    print(f'slot {i} {row["kind"]}/{row["tag"]}: merge visible={btn.isVisibleTo(size)}'
          f' text={btn.text()!r}')
    print('   source_at =', row.get('source_at'))
    if btn.isVisibleTo(size):
        print('   tip:', btn.toolTip().replace('\n', ' / '))
print('min width', size.minimumSizeHint().width())

# —— 确认框 ——
from storygraph.story.at_merge import plan_at_merge             # noqa: E402
from storygraph.ui_qt.merge_dialog import MergeDialog           # noqa: E402

beats = list(win._session.beats)
plan = plan_at_merge(beats, size.rows()[0]['source'], kind='scene',
                     label='背景')
print('plan ok', plan.ok, 'steps', plan.count, 'skipped', plan.skipped_count)
for skip in plan.skipped:
    print('   skip', skip.label, skip.reason)
print(plan.diff_text())
dlg = MergeDialog(plan)
dlg.setMinimumWidth(520)
dlg.adjustSize()
dlg.show()
for _ in range(10):
    app.processEvents()
dlg.grab().save('tools/_merge_dialog.png')
dlg.close()
win.hide()
print('saved tools/_merge_panel.png tools/_merge_dialog.png')
