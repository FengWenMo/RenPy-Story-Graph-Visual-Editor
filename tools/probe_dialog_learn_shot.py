"""把「自定义模块」弹窗渲染成 PNG（offscreen Qt）—— 人眼验收用。

    python tools/probe_dialog_learn_shot.py [输出路径]
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication                     # noqa: E402

from storygraph.story import statement_registry as SR          # noqa: E402
from storygraph.ui_qt.dialog_learn import CustomStatementDialog  # noqa: E402

# 造一个"作者真有自定义写法"的项目
td = Path(tempfile.mkdtemp(prefix='learnshot_'))
game = td / 'game'
game.mkdir(parents=True)
(game / 'ch01.rpy').write_text('''label ch01:
    swim_suit lyj "泳装" intensity=3
    lyj "第一句"
    mycue "闪电" intensity=2:
        lyj "块里的台词"
    swim_suit lyj "泳装2" intensity=1
    return
''', encoding='utf-8')
SR.clear_cache()

app = QApplication.instance() or QApplication([])
discovered = SR.discover_undeclared(td)
dlg = CustomStatementDialog(None, project_root=td, discovered=discovered,
                            registry_report=SR.format_registry_report(None))
dlg.found.setCurrentCell(0, 0)              # 点第一条 → 名字自动填好
dlg.label.setText('泳装切换')
dlg.params.add_param('intensity', '强度', '1,2,3')
dlg.show()
app.processEvents()
out = Path(sys.argv[1]) if len(sys.argv) > 1 else (
    ROOT / '.pip-tmp' / 'shots' / 'custom_statement.png')
out.parent.mkdir(parents=True, exist_ok=True)
print(f'发现 {len(discovered)} 个：{[r["head"] for r in discovered]}')
print('截图', 'ok' if dlg.grab().save(str(out)) else '失败', '->', out)
