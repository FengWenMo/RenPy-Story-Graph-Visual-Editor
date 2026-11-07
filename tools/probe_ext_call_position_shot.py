# -*- coding: utf-8 -*-
"""截图：扩展窗口里「技术调用的位置 / 关键字」（2026-11-07 第五轮）

    python tools/probe_ext_call_position_shot.py

产物（`docs/shots/`）：

  · `ext_call_position_2026-11-07.png` —— 选中 `rules.json` 里那条调用
    （只读投影）：字段表第 2 列就是「位置 / 关键字名」，`0` / `1` / `layer`
    各就各位，下面还有「等价于哪个转场 / 时长取第几个参数」。
  · `ext_call_editable_2026-11-07.png` —— 选中项目自己在 `storygraph.ext.json`
    里声明的那条（可改）：同一张表、同一列，这次**能编辑**。

临时项目，不碰真工程。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication                       # noqa: E402

OUT = ROOT / 'docs' / 'shots'

SCRIPT = '''\
label start:
    show lan happy
    $ my_wipe("left", 0.6, layer="screens")
    "第一句"
'''

#: 旧清单里的声明：位置 0 / 1、关键字 layer、候选、演出效果
RULES = {'calls': [{
    'head': 'my_wipe', 'label': '擦除过场', 'effect': 'Dissolve',
    'seconds_arg': 1,
    'columns': [
        {'name': '方向', 'label': '方向', 'kind': 'choice',
         'choices': ['left', 'right'], 'position': 0, 'required': True},
        {'name': '秒数', 'label': '秒数', 'kind': 'number', 'position': 1},
        {'name': '图层', 'label': '图层', 'kw': 'layer'},
    ],
}]}

#: 项目自己在扩展清单里声明的一条（能改形状的那条）
EXT = {'schema': 'storygraph.ext/1', 'extensions': [{
    'id': 'my_wipe2', 'kind': 'call', 'label': '擦除过场（项目自己声明）',
    'docs': '只声明第 3 个参数 + 关键字 layer',
    'enabled': True, 'effect': 'Fade', 'seconds_arg': 1,
    'fields': [
        {'key': '第三个', 'label': '第三个参数', 'kind': 'variable',
         'position': 2},
        {'key': '图层', 'label': '图层', 'kind': 'variable', 'kw': 'layer'},
    ],
}]}


def main() -> int:
    app = QApplication.instance() or QApplication([])          # noqa: F841
    from storygraph.ui_qt.dialog_extensions import ExtensionDialog
    from PySide6.QtCore import Qt

    td = Path(tempfile.mkdtemp(prefix='shot-ext-call-'))
    root = td / 'proj'
    (root / 'game').mkdir(parents=True)
    (root / 'game' / 'script.rpy').write_text(SCRIPT, encoding='utf-8')
    (root / 'storygraph.rules.json').write_text(
        json.dumps(RULES, ensure_ascii=False, indent=2), encoding='utf-8')
    (root / 'storygraph.ext.json').write_text(
        json.dumps(EXT, ensure_ascii=False, indent=2), encoding='utf-8')

    OUT.mkdir(parents=True, exist_ok=True)
    dlg = ExtensionDialog(None, project_root=str(root))
    dlg.resize(1180, 760)
    dlg.show()
    app.processEvents()

    def pick(ident):
        tree = dlg.tree
        for i in range(tree.topLevelItemCount()):
            top = tree.topLevelItem(i)
            for j in range(top.childCount()):
                child = top.child(j)
                if str(child.data(0, Qt.UserRole) or '') == ident:
                    tree.setCurrentItem(child)
                    app.processEvents()
                    app.processEvents()
                    return True
        return False

    def shot(name):
        path = OUT / name
        dlg.grab().save(str(path))
        print('saved', path)

    print('problems =', dlg._reg.problems)
    if pick('my_wipe'):
        print('位置列 =', [dlg.tbl.item(r, 1).text()
                          for r in range(dlg.tbl.rowCount())])
        print('可改吗 =', dlg.tbl.isEnabled())
        shot('ext_call_position_2026-11-07.png')

    if pick('my_wipe2'):
        print('位置列 =', [dlg.tbl.item(r, 1).text()
                          for r in range(dlg.tbl.rowCount())])
        print('可改吗 =', dlg.tbl.isEnabled(),
              '| 效果栏可见 =', dlg.meta_row.isVisibleTo(dlg),
              '| 效果 =', dlg.ed_effect.text(),
              '| 时长参数 =', dlg.sp_seconds.value())
        shot('ext_call_editable_2026-11-07.png')

    dlg.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
