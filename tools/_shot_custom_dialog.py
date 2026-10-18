"""把「自定义语句」弹窗的四个页面各截一张图（给人眼检查用，不改任何东西）。

用法：

    python tools/_shot_custom_dialog.py

输出到 `tools/out_custom_audit/ux6/`。它在**临时目录**里造一个最小项目
（一句未声明写法 + 一条声明 + 两组候选分栏），所以不会碰你真正的项目。

⚠ 用**默认** Qt 平台跑（不要 `QT_QPA_PLATFORM=offscreen`）：离屏后端没有字体，
截出来全是方框（实测）。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.story import statement_registry as SR           # noqa: E402
from storygraph.ui_qt.dialog_learn import CustomStatementDialog  # noqa: E402

OUT = ROOT / 'tools' / 'out_custom_audit' / 'ux6'
OUT.mkdir(parents=True, exist_ok=True)

PROJ = Path(tempfile.mkdtemp()) / 'proj'
(PROJ / 'game').mkdir(parents=True)

(PROJ / 'storygraph.rules.json').write_text(json.dumps({
    'statements': [{
        'name': 'weather', 'label': '天气',
        'columns': [{'name': '强度', 'label': '强度', 'choices': ['1', '2', '3']}],
        'pattern': [{'kind': 'choice', 'column': '强度', 'options': ['1', '2', '3']}],
    }],
    'attr_groups': [
        {'name': 'outfit', 'label': '服装', 'options': ['clothes2', 'base_swimsuit']},
        {'name': 'expressions', 'label': '表情', 'options': ['happy', 'sad', 'angry']},
    ],
    'host_hooks': {'trust': True},
}, ensure_ascii=False, indent=2), encoding='utf-8')

(PROJ / 'game' / 'ch01.rpy').write_text(
    'label ch01:\n'
    '    swim_suit 小明 泳装 开心\n'
    '    swim_suit 小红 校服 害羞\n'
    '    mycue "闪电" intensity=2:\n'
    '        renpy.pause(0.2)\n'
    '    lyj "第一句"\n',
    encoding='utf-8')

app = QApplication(sys.argv)
SR.clear_cache()
discovered = SR.discover_undeclared(PROJ)
reg = SR.StatementRegistry.for_project(PROJ)
dlg = CustomStatementDialog(None, project_root=PROJ, discovered=discovered,
                            declared_groups=list(reg.attr_groups or ()),
                            on_verify=lambda head: '通过')
dlg.resize(1060, 720)
dlg.show()
app.processEvents()


def shot(name: str):
    app.processEvents()
    dlg.grab().save(str(OUT / f'{name}.png'))
    print('saved', OUT / f'{name}.png')


shot('01_list')

# 点第一个待认领的词 → 进编辑页，填两格 + 试切
dlg.found.setCurrentCell(0, 0)
app.processEvents()
print('picked:', dlg.name.text())
dlg._go(1)
dlg.params.clear()
dlg.params.add_param('小明', '角色', '', kind='variable')
dlg.params.add_param('泳装', '服装', 'clothes2,base_swimsuit', kind='choice')
card = dlg.params._cards[1]
card._limit.setChecked(True)
dlg.sample.setText('swim_suit 小明 泳装')
dlg._refresh_cut()
app.processEvents()
print('cut:', dlg.cut_view.text())
shot('02_form')

# 缺列名的样子
dlg.params.add_param('', '', '', kind='variable')
dlg._refresh_preview()
app.processEvents()
print('status:', dlg._status.text())
shot('03_form_warn')
dlg.params.remove_current()

# 分栏页（已经在清单里，直接看）
dlg._baseline = dlg._signature()      # 截图脚本里没人能回答"要不要保存"
dlg._go(2)
app.processEvents()
shot('04_groups')

# 清单页
dlg._go(3)
app.processEvents()
shot('05_manifest')
dlg._baseline = dlg._signature()
dlg.close()
