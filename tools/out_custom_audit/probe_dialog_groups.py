"""自定义模块弹窗审计：看得到已定义的分栏吗？能删/重置吗？

    python tools/out_custom_audit/probe_dialog_groups.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

from PySide6.QtWidgets import QApplication, QPushButton          # noqa: E402

from audit_project import EngineStub, build_audit_project          # noqa: E402

PROJ = HERE / 'proj_dialog_audit'
SHOTS = HERE / 'shots'
SHOTS.mkdir(parents=True, exist_ok=True)
root = build_audit_project(PROJ)

from storygraph.story import statement_registry as SR             # noqa: E402
from storygraph.ui_qt.dialog_learn import CustomStatementDialog   # noqa: E402
from storygraph.authoring.learn_statement import group_preview     # noqa: E402

app = QApplication.instance() or QApplication([])
SR.clear_cache()
reg = SR.StatementRegistry.for_project(root)
print('rules.json 里声明了：',
      [(g.name, g.label, g.options) for g in reg.attr_groups])
report = SR.format_registry_report(reg)
print('\nformat_registry_report（弹窗第①页那段只读说明）:')
print('  ' + report.replace('\n', '\n  '))

dlg = CustomStatementDialog(
    None, project_root=root, discovered=[],
    registry_report=report,
    declared_groups=reg.attr_groups)
dlg.resize(1100, 760)
dlg.show()
app.processEvents()

print('\n第①页只读框（`dlg.declared`）全文：')
print('  ' + dlg.declared.toPlainText().replace('\n', '\n  '))
print('\n第①页是否出现「服装」/「动作」/attr_groups：',
      '服装' in dlg.declared.toPlainText(),
      '动作' in dlg.declared.toPlainText(),
      'attr_groups' in dlg.declared.toPlainText())

dlg.tabs.setCurrentIndex(0)
app.processEvents()
p1 = SHOTS / '08_dialog_page1_declared.png'
dlg.grab().save(str(p1))
print('第①页截图：', p1)

print('\n第③页的栏（`columns_editor.rows()`）：')
for r in dlg.columns_editor.rows():
    print('  ', r)
dlg.tabs.setCurrentIndex(2)
app.processEvents()
p3 = SHOTS / '09_dialog_page3_columns.png'
dlg.grab().save(str(p3))
print('第③页截图：', p3)

all_buttons = [b.text() for b in dlg.findChildren(QPushButton) if b.text()]
print('\n弹窗里所有按钮：', all_buttons)
print('有没有“重置 / 清空分组 / 删除整份声明”：',
      [t for t in all_buttons if any(k in t for k in ('重置', '清空', '删除'))])

print('\n默认「把分栏一起写进清单」勾了吗：', dlg.write_columns.isChecked())
print('不勾时 groups_to_write() =', dlg.groups_to_write())

# —— 模拟：用户删掉「神态」那一栏，再按“写进清单” ——
dlg.write_columns.setChecked(True)
before = json.loads((root / 'storygraph.rules.json').read_text(encoding='utf-8'))
print('\n删之前文件里的 attr_groups：',
      [g['name'] for g in before.get('attr_groups', [])])
# 删掉最后一栏（expressions/神态）
dlg._remove_column()
app.processEvents()
print('界面剩下：', [(r['key'], r['shown']) for r in dlg.columns_editor.rows()])
rows = dlg.groups_to_write()
print('groups_to_write() =', rows)
res = SR.declare_attr_groups(root, rows)
after = json.loads((root / 'storygraph.rules.json').read_text(encoding='utf-8'))
print('写盘结果：', res)
print('删之后文件里的 attr_groups：',
      [g['name'] for g in after.get('attr_groups', [])])

# —— 模拟：全部清空 ——
dlg.columns_editor.clear()
app.processEvents()
print('\n全部清空后 groups_to_write() =', dlg.groups_to_write())
res_empty = SR.declare_attr_groups(root, [])
print('declare_attr_groups(root, []) =', res_empty)
print('文件还在：', (root / 'storygraph.rules.json').exists())

# —— 中文“分栏标识”会被 build_group 拒绝，而预览里什么都不说 ——
dlg2 = CustomStatementDialog(None, project_root=root, discovered=[],
                             registry_report=report)
dlg2.columns_editor.clear()
dlg2.columns_editor.add_column('服装', '服装', '泳装、校服')
dlg2.write_columns.setChecked(True)
built = dlg2.built_groups()
print('\n分栏标识写「服装」时 built_groups() =', built)
print('groups_to_write() =', dlg2.groups_to_write())
print('group_preview() =', group_preview(built))
dlg2.resize(1100, 760)
dlg2.tabs.setCurrentIndex(2)
dlg2.show()
app.processEvents()
p4 = SHOTS / '10_dialog_chinese_key_silent.png'
dlg2.grab().save(str(p4))
print('截图：', p4)

dlg.close()
dlg2.close()
print('\n完成。截图目录：', SHOTS)
