"""看一眼：载入那份存量声明之后，角色那一格和提示长什么样（离屏截图）。

跑法：`python tools/_shot_role_fix.py`
产物：`.workbuddy/shots/role_fix_dialog.png`（只读真机项目，不改它）
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(r'D:\千连万理\RenPyTest\sr_general')
OUT = Path(__file__).resolve().parents[1] / '.workbuddy' / 'shots' / 'role_fix_dialog.png'


def main() -> int:
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])      # noqa: F841
    from storygraph.ui_qt.dialog_learn import CustomStatementDialog

    raw = json.loads((ROOT / 'storygraph.rules.json').read_text(encoding='utf-8'))
    data = (raw.get('statements') or [{}])[0]
    decl = SimpleNamespace(name=data.get('name', ''), label='', docs='',
                           block=None, columns=data.get('columns') or [],
                           pattern=None)
    dlg = CustomStatementDialog(None, project_root=str(ROOT), discovered=[])
    dlg._load_statement(decl)
    dlg._go(1)                                     # PAGE_FORM
    dlg.sample.setText('show lan base_swimsuit happy')
    dlg._refresh_cut()
    dlg.resize(1100, 900)
    dlg.show()
    app.processEvents()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    dlg.grab().save(str(OUT))
    print('saved', OUT, OUT.stat().st_size, 'bytes')
    print('cut :', dlg.cut_view.text())
    print('note:', dlg._role_fix_note.text())
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
