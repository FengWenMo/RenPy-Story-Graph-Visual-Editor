"""把角色决策弹窗渲染成 PNG（offscreen Qt）—— 人眼验收用。

    python tools/probe_dialog_chars_shot.py [输出路径]
"""

import os
import sys
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication                        # noqa: E402

from storygraph.authoring.char_decisions import proposals          # noqa: E402
from storygraph.authoring.txt_import import CharacterMap, convert_text  # noqa: E402
from storygraph.ui_qt.dialog_chars import CharDecisionDialog       # noqa: E402

REAL = Path(r'D:\千连万理\RenpyTest\sr_general')
from storygraph.pipeline.build import load_asset_registry          # noqa: E402

rows = (load_asset_registry(REAL) or {}).characters if REAL.is_dir() else \
    {'jc': {'ID': 'jc', 'Name': '「蒋忱」'}, 'mm': {'ID': 'mm', 'Name': '妈妈'},
     'txf': {'ID': 'txf', 'Name': '汤X凤'}, 'lyj': {'ID': 'lyj', 'Name': '「凌跃捷」'}}

text = ('1.0 chapter1-0 开学\n'
        'MOM"跟妈客气啥，上班顺手的事儿。"\n'
        'MOM"同学之间一定要和睦相……"\n'
        'A"卷王——"\n'
        'LINGDAO"金秋九月，丹桂飘香……"\n'
        'JIAOGUAN"预备——"\n'
        'XIAOYI"同学早上吃早餐了吗"\n'
        'BAOAN"小伙子，你要去哪个地方？"\n'
        'TJF"你怎么睡得着哒？"\n')

app = QApplication.instance() or QApplication([])
chars = CharacterMap(rows)
conv = convert_text(text, characters=chars)
props = proposals(conv, chars, rows)
dlg = CharDecisionDialog(None, props, rows)
dlg.resize(1100, 620)
dlg.show()
app.processEvents()
out = Path(sys.argv[1]) if len(sys.argv) > 1 else (
    ROOT / '.pip-tmp' / 'shots' / 'char_decision_dialog.png')
out.parent.mkdir(parents=True, exist_ok=True)
ok = dlg.grab().save(str(out))
print(f'行数={dlg.table.rowCount()}  截图 {"ok" if ok else "失败"} -> {out}')
for i, ed in enumerate(dlg._editors):
    print(f'  [{ed.proposal.token:9s}] 动作={ed.action.currentText():6s} '
          f'目标={ed.target.currentData()!r}  说明={ed.note.text()[:60]}')
