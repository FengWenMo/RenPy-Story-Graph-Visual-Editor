# -*- coding: utf-8 -*-
"""截图：试切说清"为什么切不上"（修前/修后对照用）。

    python tools/out_custom_audit/shot_cut_explain.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))

OUT = HERE / 'after_fix'
OUT.mkdir(parents=True, exist_ok=True)

from PySide6.QtWidgets import QApplication                        # noqa: E402

from storygraph.story import statement_registry as SR              # noqa: E402
from storygraph.ui_qt.dialog_learn import CustomStatementDialog    # noqa: E402

PROJ = Path(r'D:\千连万理\RenPyTest\sr_general')
ROLES = ('lan, lyj, ling, ye, mm, dhy, narrator, q, xing, xy')
OUTFIT = ('clothes2, base_hand_up, base_swimsuit, hanfu_base, base_handdown')
FACE = 'happy, sad, smile, angry, shy'

app = QApplication.instance() or QApplication([])
SR.clear_cache()


def make(kind_outfit='choice', outfit_literal='', outfit_name='服装',
         sample='show lan base_swimsuit happy'):
    dlg = CustomStatementDialog(None, project_root=PROJ, discovered=[],
                                registry_report=SR.format_registry_report(None))
    dlg.name.setText('show')
    dlg.params.clear()
    dlg.params.add_param('角色', '角色', ROLES, kind='choice')._limit.setChecked(True)
    outfit = dlg.params.add_param(outfit_name, '服装', OUTFIT, kind=kind_outfit)
    if outfit_literal:
        outfit._literal.setText(outfit_literal)
    face = dlg.params.add_param('表情', '表情', FACE, kind='choice')
    face._limit.setChecked(True)
    dlg.resize(1180, 820)
    dlg.show()
    dlg.tabs.setCurrentIndex(1)
    app.processEvents()
    dlg.sample.setText(sample)
    dlg._refresh_cut()
    app.processEvents()
    bar = dlg.declare_scroll.verticalScrollBar()
    bar.setValue(bar.maximum())
    app.processEvents()
    return dlg, outfit


# ① 死格：可选段没写固定字 —— 现在第一句就点它
dlg, outfit = make(kind_outfit='optional', outfit_literal='', outfit_name='')
print('死格：', dlg.cut_view.text())
dlg.grab().save(str(OUT / 'cut_dead_slot.png'))
# 同一张卡往上滚一点：让"这一格固定是哪几个字"这个新输入框露出来
dlg.declare_scroll.ensureWidgetVisible(outfit)
app.processEvents()
dlg.grab().save(str(OUT / 'cut_literal_field.png'))
dlg.close()

# ② 修好之后：把固定字补上，同一行照样能切 / 或者按"从这些里选"
dlg = make(kind_outfit='choice', outfit_name='服装')
print('正常：', dlg.cut_view.text())
dlg.grab().save(str(OUT / 'cut_ok.png'))
dlg.close()

print('截图：', OUT / 'cut_dead_slot.png', OUT / 'cut_ok.png')
