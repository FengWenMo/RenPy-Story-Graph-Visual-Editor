"""把「分组」的两处界面渲染成 PNG：① 右键候选列表（带组标题）② 自定义模块弹窗。

    python tools/probe_group_shots.py
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication                          # noqa: E402

from storygraph.authoring.spec_groups import parse_attr_groups       # noqa: E402
from storygraph.story import statement_registry as SR                # noqa: E402
from storygraph.ui_qt.choice_picker import ChoicePickerDialog        # noqa: E402
from storygraph.ui_qt.dialog_learn import CustomStatementDialog      # noqa: E402

app = QApplication.instance() or QApplication([])
out = ROOT / '.pip-tmp' / 'shots'
out.mkdir(parents=True, exist_ok=True)

# ① 候选列表（真实项目的 lan 那 32 个属性，按"服装 / 表情 / 其他"分栏）
groups = [
    {'group': 'outfit', 'label': '服装', 'values': ['clothes2']},
    {'group': 'expression', 'label': '表情',
     'values': ['angry', 'angry2', 'close_happy', 'happy', 'shy', 'sigh',
                'lan_angry', 'lan_happy', 'lan_shy']},
    {'group': '', 'label': '', 'values': ['afraid', 'agreeable', 'arrogant',
                                          'base_hand_up', 'chuckle', 'stare']},
]
dlg = ChoicePickerDialog(None, title='选择表情 —— 共 16 个候选（2 组）',
                         choices=[v for g in groups for v in g['values']],
                         current='happy', groups=groups)
dlg.show()
app.processEvents()
p1 = out / 'grouped_picker.png'
print('候选列表截图', 'ok' if dlg.grab().save(str(p1)) else '失败', '->', p1)

# ② 自定义模块弹窗（词组那一节）
td = Path(tempfile.mkdtemp(prefix='groupshot_'))
(td / 'game').mkdir(parents=True)
(td / 'game' / 'ch01.rpy').write_text(
    'label ch01:\n    swim_suit lyj "泳装" intensity=3\n    return\n',
    encoding='utf-8')
SR.clear_cache()
dlg2 = CustomStatementDialog(
    None, project_root=td, discovered=SR.discover_undeclared(td),
    registry_report=SR.format_registry_report(
        SR.StatementRegistry.for_project(td)),
    declared_groups=parse_attr_groups([
        {'name': 'outfit', 'label': '服装', 'options': ['clothes2', 'swim_*']},
        {'name': 'expression', 'label': '表情',
         'options': ['happy', 'shy', 'sigh']},
        {'name': 'rest', 'label': '其他'}]))
dlg2.name.setText('swim_suit')
dlg2.label.setText('泳装切换')
# ⚠ 出图要**多填几个参数** —— 用户抱怨的正是"多个参数还要下拉看"，
#   一个参数看不出这个问题（旧版单参数时表格看着还行）。
dlg2.params.clear()
dlg2.params.add_param('intensity', '强度', '1,2,3')
dlg2.params.add_param('expression', '表情', 'happy,shy,sigh,angry')
dlg2.params.add_param('who', '对谁说', 'lyj,lan,xing')
dlg2.write_groups.setChecked(True)
dlg2.groups.clear()
dlg2.groups.add_group('pose', '姿势', 'stand, sit')
dlg2.groups.add_group('outfit', '服装', 'clothes2, swim_*')
dlg2.show()
app.processEvents()
p2 = out / 'learn_dialog_groups.png'
print('弹窗截图', 'ok' if dlg2.grab().save(str(p2)) else '失败', '->', p2)
print('词组预览：')
print(dlg2.preview.toPlainText()[-260:])
