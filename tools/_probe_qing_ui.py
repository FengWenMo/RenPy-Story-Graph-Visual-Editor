r"""真机选择器两张截图（§102 qing 那种"角色表没登记、但有立绘"的人）。

    $env:QT_QPA_PLATFORM='offscreen'; $env:QT_QPA_FONTDIR='C:\Windows\Fonts'
    python tools/_probe_qing_ui.py

为什么 `QT_QPA_FONTDIR` 是必需的：offscreen 平台走 freetype 字体库，不指这个
变量的话**一个汉字都渲染不出来**（满屏方块），而"界面到底显示成什么"正是这
两张图要证明的东西。

产出（仓里各存一份，便于对照 §102 的结论）：

  · `tools/_qing_picker_sprites.png` —— 左栏出现「立绘：qing」，四个格子是
    `qing angry / qing base / qing curious / qing en`（**声明过的属性词**）；
  · `tools/_qing_picker_preview.png` —— 搜 `qing` 选那张**预览图**：
    详情说它"不能直接写进 show"，提示条说"已用对应的立绘「qing」
    （qing curious）；将写入：qing curious"，确定可点。
"""
from __future__ import annotations

import os
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')

from PySide6.QtCore import QSettings                    # noqa: E402
from PySide6.QtWidgets import QApplication               # noqa: E402

from storygraph.assets.registry_load import load_asset_registry  # noqa: E402
from storygraph.ui_qt import asset_picker as AP          # noqa: E402

ROOT = r'D:\千连万理\sr_general'
HERE = os.path.dirname(__file__)

reg = load_asset_registry(ROOT)
app = QApplication.instance() or QApplication([])
AP.settings_factory = lambda: QSettings(
    os.path.join(os.environ.get('TEMP', '.'), 'dsh_probe_qing.ini'),
    QSettings.IniFormat)
dlg = AP.AssetPickerDialog(None, registry=reg, project_root=ROOT,
                           types=['image'], want_image_spec=True,
                           title='选择立绘')
dlg.resize(1240, 800)
dlg.show()
for _ in range(20):
    app.processEvents()


def _ids():
    return [dlg._items.item(i).data(0x0100)[1]
            for i in range(dlg._items.count())]


def _select(asset_id):
    for i in range(dlg._items.count()):
        it = dlg._items.item(i)
        if it.data(0x0100)[1] == asset_id:
            dlg._items.setCurrentItem(it)
            return True
    return False


def _settle(rounds=60):
    for _ in range(rounds):
        app.processEvents()
        time.sleep(0.02)


# ---- ① 立绘组：立绘：qing + 声明过的属性词 -----------------------------
groups = [dlg._cats.item(i).text() for i in range(dlg._cats.count())]
dlg._cats.setCurrentRow(groups.index('立绘：qing'))
_settle(20)
print('① 网格:', _ids())
print('  选中:', _select('characters_stare'), '| 提示:', dlg._pick_hint.text())
_settle()
dlg.grab().save(os.path.join(HERE, '_qing_a.png'))

# ---- ② 预览图：自动换成真立绘 ----------------------------------------
dlg._cats.setCurrentRow(groups.index('全部'))
dlg._search.setText('qing')
_settle(20)
print('② 搜索 qing 后网格:', _ids())
print('  选中预览图:', _select('qing_stare'), '| 提示:', dlg._pick_hint.text())
_settle()
dlg.grab().save(os.path.join(HERE, '_qing_b.png'))
print('确定可点:', dlg._ok.isEnabled())
