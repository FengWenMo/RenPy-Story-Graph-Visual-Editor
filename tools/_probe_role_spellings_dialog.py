"""探针：存量声明（角色候选=显示名）载入弹窗之后，试切还认不认得 `show lan …`。

跑法：`python tools/_probe_role_spellings_dialog.py`（Qt 走 offscreen，只读真机项目）
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import json                          # noqa: E402

ROOT = Path(r'D:\千连万理\RenPyTest\sr_general')


def main() -> int:
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])      # noqa: F841
    from storygraph.ui_qt.dialog_learn import CustomStatementDialog

    data = json.loads((ROOT / 'storygraph.rules.json').read_text(encoding='utf-8'))
    raw = (data.get('statements') or [{}])[0]
    decl = SimpleNamespace(name=raw.get('name', ''), label='', docs='',
                           block=None, columns=raw.get('columns') or [],
                           pattern=None)

    dlg = CustomStatementDialog(None, project_root=str(ROOT), discovered=[])
    dlg._load_statement(decl)
    card = dlg.params._cards[0]
    from PySide6.QtWidgets import QPushButton
    chips = [b.text() for b in card._values.findChildren(QPushButton)]
    print('第 1 格列名 :', card.data().get('label'))
    print('候选个数    :', len(card._values._values))
    print('芯片长这样  :', chips[:6], '…')
    print('写出去的值  :', card.data().get('choices')[:120])
    print('提示        :', dlg._role_fix_note.text()[:200])
    print('提示可见    :', dlg._role_fix_note.isVisible())

    dlg.sample.setText('show lan base_swimsuit happy')
    dlg._refresh_cut()
    print('\n试切        :', dlg.cut_view.text())
    ok = '切不上' not in dlg.cut_view.text() and '切出来是这样' in dlg.cut_view.text()
    print('\n结论        :', '✅ 切得上' if ok else '❌ 还是切不上')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
