r"""真机截图：按**新扫描结果**建表后，选择器的分组长什么样（§103）。

    $env:QT_QPA_PLATFORM='offscreen'; $env:QT_QPA_FONTDIR='C:\Windows\Fonts'
    python tools/_probe_cat_picker.py

⚠ 只读：扫描结果在内存里建一个注册表给选择器用，**不写**项目自己的
`asset_tables/*.xlsx`（用户点「刷新」之前，磁盘上的表还是旧的）。
"""
from __future__ import annotations

import os
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')

from PySide6.QtCore import QSettings                    # noqa: E402
from PySide6.QtWidgets import QApplication               # noqa: E402

from storygraph.assets.asset_registry import (AssetRegistry,  # noqa: E402
                                              scan_assets)
from storygraph.assets.registry_load import load_asset_registry  # noqa: E402
from storygraph.ui_qt import asset_picker as AP          # noqa: E402

ROOT = r'D:\千连万理\sr_general'
HERE = os.path.dirname(__file__)

old = load_asset_registry(ROOT)
res = scan_assets(ROOT, include_ui=True)
reg = AssetRegistry()
reg.images = {row['ID']: row for row in res['images']}
reg.characters = dict(getattr(old, 'characters', None) or {})

app = QApplication.instance() or QApplication([])
AP.settings_factory = lambda: QSettings(
    os.path.join(os.environ.get('TEMP', '.'), 'dsh_probe_cat.ini'),
    QSettings.IniFormat)
dlg = AP.AssetPickerDialog(None, registry=reg, project_root=ROOT,
                           types=['image'], want_image_spec=True,
                           title='选择资产')
dlg.resize(1240, 800)
dlg.show()
for _ in range(20):
    app.processEvents()

names = [dlg._cats.item(i).text() for i in range(dlg._cats.count())]
print('分组:', names)


def _count(name):
    dlg._cats.setCurrentRow(names.index(name))
    for _ in range(15):
        app.processEvents()
    return dlg._items.count()


for name in ('背景', '其他图片', '未识别（不猜）'):
    print('%-14s %d' % (name, _count(name)))

dlg._cats.setCurrentRow(names.index('背景'))
for _ in range(40):
    app.processEvents()
    time.sleep(0.02)
dlg.grab().save(os.path.join(HERE, '_cat_picker_bg.png'))
print('截图 ->', os.path.join(HERE, '_cat_picker_bg.png'))
