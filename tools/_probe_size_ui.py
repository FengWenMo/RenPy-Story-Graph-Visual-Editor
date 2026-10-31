# -*- coding: utf-8 -*-
"""临时探针：卡片「尺寸」一格的截图（offscreen）。"""
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

# 只有一条 zoom 0.5：铺满 / 适应都匹配不到 → 面板该列「生成 sg_*」
transform half:
    zoom 0.5

label ch01:
    scene bg big
    show cg cg1 at half
    e "这一拍的背景是 4K 超清图，游戏里会被裁切。"
    e "项目里没有铺满的 transform，面板应当提议生成一条。"
'''

GUI = '''init python:
    gui.init(1920, 1080)

define gui.textbox_height = 278
define gui.textbox_yalign = 1.0
define gui.text_size = 33
define gui.name_text_size = 45
'''

root = Path(tempfile.mkdtemp()) / 'proj'
game = root / 'game'
(game / 'script').mkdir(parents=True)
(game / 'images' / 'bg').mkdir(parents=True)
(game / 'images' / 'cg').mkdir(parents=True)
(game / 'gui.rpy').write_text(GUI, encoding='utf-8')
(game / 'script' / 'ch01.rpy').write_text(SCRIPT, encoding='utf-8')

Image.new('RGB', (3840, 2160), (40, 90, 140)).save(
    game / 'images' / 'bg' / 'big.png')
img = Image.new('RGB', (1600, 900), (200, 120, 40))
img.save(game / 'images' / 'cg' / 'cg1.png')

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

out = Path('tools/_size_panel_shot.png')
win.grab().save(str(out))
panel = win._panel
panel.grab().save(str(out.with_name('_size_panel_card.png')))
# 第二张：把 CG 那一行切到「生成」项，看长文本会不会被截断、按钮会不会亮
size = win._panel.size_panel()
rows = size.rows()
if len(rows) > 1:
    gen = next((i for i, it in enumerate(rows[1]['items'])
                if it.get('generate')), -1)
    if gen >= 0:
        size.slots()[1]['combo'].setCurrentIndex(gen)
        for _ in range(10):
            app.processEvents()
        panel.grab().save(str(out.with_name('_size_panel_gen.png')))
        print('generate item:', rows[1]['items'][gen]['text'],
              '| button enabled:', size.slots()[1]['button'].isEnabled())
print('saved', out, '| beats', len(win._session.beats))
for row in win._panel.size_panel().rows():
    print('row', row['kind'], row['tag'], '|', row['note'])
    for it in row['items'][:6]:
        print('   ', it['ok'], it['current'], it['text'])
win.hide()
