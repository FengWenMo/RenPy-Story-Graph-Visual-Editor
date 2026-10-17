# -*- coding: utf-8 -*-
"""复现：试切说"候选外"，但预览里明明列着这些候选。

    python tools/out_custom_audit/probe_cut_now.py
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

from PySide6.QtWidgets import QApplication                        # noqa: E402

from storygraph.story import statement_registry as SR              # noqa: E402
from storygraph.ui_qt.dialog_learn import CustomStatementDialog    # noqa: E402

PROJ = Path(r'D:\千连万理\RenPyTest\sr_general')

ROLES = ('aaa, aabb, ay, ba, bbb, bd, dhy, dhyab, dororo, fw, hidden_name, '
         'jc, jcab, jg, lan, ld, ling, ly, mm, narrator, q, toto, txf, u, '
         'xing, xy, ye')
OUTFIT = ('clothes2, base_hand_up, base_swimsuit, base_arms_akimbo, '
          'base_hands_down, hanfu_base, base_handdown')
FACE = ('afraid, agreeable, angry, happy, sad, smile, wink')

app = QApplication.instance() or QApplication([])
SR.clear_cache()


def build(kind_outfit='choice', kind_face='choice', limit_face=True,
          limit_outfit=False, multi_outfit=False, face_choices=FACE,
          outfit_name='服装'):
    dlg = CustomStatementDialog(None, project_root=PROJ, discovered=[],
                                registry_report=SR.format_registry_report(None))
    dlg.name.setText('show')
    dlg.params.clear()
    c1 = dlg.params.add_param('角色', '角色', ROLES, kind='choice')
    c1._limit.setChecked(True)
    c2 = dlg.params.add_param(outfit_name, '服装', OUTFIT, kind=kind_outfit)
    c2._limit.setChecked(limit_outfit)
    c2._multi.setChecked(multi_outfit)
    c3 = dlg.params.add_param('表情', '表情', face_choices, kind=kind_face)
    c3._limit.setChecked(limit_face)
    dlg.sample.setText('show lan base_swimsuit happy')
    dlg._refresh_cut()
    app.processEvents()
    return dlg


def report(tag, **kw):
    dlg = build(**kw)
    print('=' * 78)
    print('##', tag)
    print('   预览首行（候选来自 params）：')
    for ln in dlg.preview.toPlainText().splitlines():
        if '候选' in ln:
            print('     ', ln[:100] + ('…' if len(ln) > 100 else ''))
    print('   试切：', dlg.cut_view.text().splitlines()[0])
    print('   slots：', [(s['column'], s['kind'], s['limit'], s['multi'],
                        len(s.get('choices') or ''))
                       for s in dlg.params.slots()])
    print('   分栏（built_groups）：', [(g.get('name'), g.get('ok'),
                                      len(g.get('options') or []))
                                     for g in dlg.built_groups()])
    dlg.close()


report('① 三格都是 choice，只有表情勾了"限死"')
report('② 服装勾了"可以有多个"', multi_outfit=True)
report('③ 表情勾了"限死"，但候选里没有 happy', face_choices='afraid, sad')
report('④ 服装是 optional（截图那台旧版本能对上的一种形状）',
       kind_outfit='optional')
report('④b 服装是 optional，且**没写固定字**（真·死格，用户现场）',
       kind_outfit='optional', outfit_name='')

# ⑤ 修复后：optional 补上"是哪几个字" → 真的能切
dlg = build(kind_outfit='optional', outfit_name='')
cards = list(dlg.params._cards)
cards[1]._literal.setText('at')
cards[1]._name.setText('位置')
dlg.params.remove_current()          # 去掉"表情"那格（这一行里没有它）
dlg.sample.setText('show lan at left')
dlg._refresh_cut()
app.processEvents()
print('=' * 78)
print('## ⑤ optional 填了固定字「at」')
print('   试切：', dlg.cut_view.text().splitlines()[0])
print('   预览里那一行：', [ln for ln in dlg.preview.toPlainText().splitlines()
                            if '位置' in ln or '服装' in ln])
dlg.close()

# ⑥ keyword 的固定字与列名分开了：列名不再被迫写成 show
dlg = build()
dlg.params.clear()
kw = dlg.params.add_field('', '', '', kind='keyword')
kw._literal.setText('show')
kw._name.setText('动作')
c1 = dlg.params.add_param('角色', '角色', ROLES, kind='choice')
c1._limit.setChecked(True)
c2 = dlg.params.add_param('服装', '服装', OUTFIT, kind='choice')
c3 = dlg.params.add_param('表情', '表情', FACE, kind='choice')
c3._limit.setChecked(True)
dlg.sample.setText('show lan base_swimsuit happy')
dlg._refresh_cut()
app.processEvents()
print('=' * 78)
print('## ⑥ keyword：列名「动作」、固定字「show」')
print('   试切：', dlg.cut_view.text().splitlines()[0])
print('   slots[0]：', dlg.params.slots()[0])
print('   预览：', [ln for ln in dlg.preview.toPlainText().splitlines()
                    if '动作' in ln])
dlg.close()
