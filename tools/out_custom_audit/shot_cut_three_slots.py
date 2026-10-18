# -*- coding: utf-8 -*-
"""截图：三格（角色 / 服装 / 表情）试切 `show lan base_swimsuit happy`。

复现用户 2026-10-17 的现场（修复前：`lan base_swimsuit ｜ 空 ｜ happy`）：
角色与服装**都**勾了"这一格可以有多个"，两格相邻时旧代码把服装的候选词
当成角色的第二个值吃掉了。

    python tools/out_custom_audit/shot_cut_three_slots.py
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
ROLES = ('aaa, aabb, ay, ba, bbb, bd, dhy, dhyab, dororo, fw, hidden_name, '
         'jc, jcab, jg, lan, ld, ling, ly, mm, narrator, q, toto, txf, u, '
         'xing, xy, ye')
OUTFIT = ('clothes2, base_hand_up, base_swimsuit, base_arms_akimbo, '
          'base_hands_down, hanfu_base, base_handdown')
FACE = ('afraid, agreeable, angry, arrogant, chuckle, happy, sad, shy, '
        'smile, sleepy, wink')

app = QApplication.instance() or QApplication([])
SR.clear_cache()


def build(sample: str = 'show lan base_swimsuit happy'):
    dlg = CustomStatementDialog(None, project_root=PROJ, discovered=[],
                                registry_report=SR.format_registry_report(None))
    dlg.name.setText('show')
    dlg.params.clear()
    role = dlg.params.add_param('角色', '角色', ROLES, kind='variable')
    role._multi.setChecked(True)               # 「这一格可以有多个」
    outfit = dlg.params.add_param('服装', '服装', OUTFIT, kind='choice')
    outfit._multi.setChecked(True)             # 两格都多值 —— 用户现场的坑
    dlg.params.add_param('表情', '表情', FACE, kind='choice')
    dlg.resize(1180, 900)
    dlg.show()
    dlg.tabs.setCurrentIndex(1)
    app.processEvents()
    dlg.sample.setText(sample)
    dlg._refresh_cut()
    app.processEvents()
    dlg.declare_scroll.ensureWidgetVisible(dlg.cut_view)
    app.processEvents()
    return dlg


if __name__ == '__main__':
    from PySide6.QtCore import QPoint

    dlg = build()
    print('试切：', dlg.cut_view.text())
    dlg.resize(1180, 1300)
    app.processEvents()
    dlg.grab().save(str(OUT / 'cut_three_slots.png'))
    # 只截 C（试切）+ D（预览）那一块 —— 与用户截图同视角
    viewport = dlg.declare_scroll.viewport()
    y = dlg.cut_view.mapTo(viewport, QPoint(0, 0)).y()
    pix = viewport.grab()
    crop = pix.copy(0, max(0, y - 96), pix.width(), pix.height() - max(0, y - 96))
    crop.save(str(OUT / 'cut_three_slots_crop.png'))
    print('裁剪起点：', y, '尺寸：', crop.width(), crop.height())
    dlg.close()
    print('截图：', OUT / 'cut_three_slots.png')

