"""审计新增的第④页「已有声明（查看 / 删除 / 重置）」以及新的保存路径。

    python tools/out_custom_audit/probe_dialog_manage.py
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

from PySide6.QtWidgets import QApplication, QMessageBox          # noqa: E402

from audit_project import build_audit_project                     # noqa: E402

PROJ = HERE / 'proj_dialog_manage'
SHOTS = HERE / 'shots'
SHOTS.mkdir(parents=True, exist_ok=True)
root = build_audit_project(PROJ)

from storygraph.story import statement_registry as SR              # noqa: E402
from storygraph.ui_qt.dialog_learn import CustomStatementDialog    # noqa: E402

app = QApplication.instance() or QApplication([])


def manifest():
    return json.loads((root / 'storygraph.rules.json').read_text(encoding='utf-8'))


def groups():
    return [g['name'] for g in manifest().get('attr_groups', [])]


# 先加一条语句声明，让第④页两种类型都有东西可看
SR.declare_statement(root, 'swim_suit', label='泳装切换',
                     columns=[{'name': 'pose', 'label': '姿势',
                               'choices': ['happy', 'sad']}])
SR.clear_cache()
reg = SR.StatementRegistry.for_project(root)
dlg = CustomStatementDialog(None, project_root=root, discovered=[],
                            registry_report=SR.format_registry_report(reg),
                            declared_groups=reg.attr_groups)
dlg.resize(1100, 820)
dlg.show()
app.processEvents()
dlg.tabs.setCurrentIndex(3)
app.processEvents()

print('=' * 80)
print('① 第④页只读视图（用户终于能看到清单里有什么）')
print(dlg.manage_view.toPlainText())
print('\n按钮：', [b.text() for b in dlg.findChildren(type(dlg._manage_del))
                   if b.text()])
print('manage_list 条目：', [dlg.manage_list.item(i).text()
                            for i in range(dlg.manage_list.count())])
p4 = SHOTS / '13_dialog_page4_manage.png'
dlg.grab().save(str(p4))
print('截图：', p4)

# 让 QMessageBox.question 一律回答 Yes（只替换这一层，不碰 C++ 模态）
orig_q = QMessageBox.question
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
try:
    print('\n' + '=' * 80)
    print('② 「只保存候选分栏」= 整段替换：删掉的一栏真的会消失吗？')
    print('   删之前：', groups())
    dlg.columns_editor.remove_current()      # 删掉最后一栏（神态）
    app.processEvents()
    dlg._save_columns_now()
    SR.clear_cache()
    print('   删之后：', groups())
    print('   备份目录：', sorted(p.name for p in
                                 (root / 'storygraph.rules.backup').glob('*.json')))

    print('\n' + '=' * 80)
    print('③ 「清空候选分栏」：语句声明会保留吗？')
    before = manifest()
    dlg._refresh_manage()
    dlg._clear_groups()
    SR.clear_cache()
    after = manifest()
    print('   清空后 attr_groups：', after.get('attr_groups'))
    print('   语句声明还在吗：', [s.get('name') for s in after.get('statements', [])])
    print('   其它键还在吗：', [k for k in after if k not in
                              ('statements', 'attr_groups')])

    print('\n' + '=' * 80)
    print('④ 「重置所有自定义声明」：两段都清、备份、保留其它键')
    (root / 'storygraph.rules.json').write_text(json.dumps({
        'statements': [{'name': 'swim_suit'}],
        'attr_groups': [{'name': 'outfit', 'label': '服装'}],
        '我的备注': '别弄丢我',
        'host_hooks': {'trust': True},
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    SR.clear_cache()
    dlg._refresh_manage()
    dlg._reset_all()
    SR.clear_cache()
    data = manifest() if (root / 'storygraph.rules.json').exists() else {}
    print('   重置后文件还在：', (root / 'storygraph.rules.json').exists())
    print('   重置后内容：', data)
    print('   备份数：', len(list((root / 'storygraph.rules.backup').glob('*.json'))))
finally:
    QMessageBox.question = orig_q

dlg.close()
print('\n完成。截图目录：', SHOTS)
