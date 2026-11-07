"""截图：**只在扩展清单里**声明的写法，卡片上到底出不出格子（端到端）。

    python tools/_probe_ext_card_shot.py

与 `_probe_code_field_card_shot.py` 的区别只有一处，但那一处是重点：
那条写法**不在** `storygraph.rules.json` 里，只写在 `storygraph.ext.json` 里 ——
也就是"用户在扩展窗口里点新建、加了几格、保存"之后磁盘上的样子。
产物 `docs/shots/card_from_ext_2026-11-07.png`。
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

#: ⚠ 没有 `storygraph.rules.json` —— 声明只在扩展清单里
EXT = {'schema': 'storygraph.ext/1', 'extensions': [
    {'id': 'flash', 'kind': 'statement', 'label': '闪电动画',
     'docs': '文字 + 一段代码 + 固定字 with + 转场表达式',
     'enabled': True,
     'fields': [
         {'key': '文字', 'kind': 'variable'},
         {'key': '片段', 'kind': 'code'},
         {'key': 'with', 'kind': 'keyword'},
         {'key': '转场', 'kind': 'transition'},
     ]},
]}

SCRIPT = '''\
label ch:
    "开场"
    flash 闪电 Dissolve(0.1, 0.2) with Fade(0.2, 0, 0.2)
    "收尾"
'''


def main() -> int:
    app = QApplication.instance() or QApplication([])
    from storygraph.ui_qt.main_window import MainWindowQt

    td = Path(tempfile.mkdtemp(prefix='shot-extcard-'))
    root = td / 'proj'
    (root / 'game').mkdir(parents=True)
    (root / 'storygraph.ext.json').write_text(
        json.dumps(EXT, ensure_ascii=False, indent=2), encoding='utf-8')
    (root / 'game' / 'ch.rpy').write_text(SCRIPT, encoding='utf-8')

    class _Engine:
        project_root = str(root)

        def get_registry(self):
            return None

    win = MainWindowQt(_Engine(), (root / 'game' / 'ch.rpy'))
    win.resize(1500, 980)
    win.show()
    app.processEvents()
    win._set_page(win.PAGE_CARD)
    app.processEvents()

    target = None
    for idx, beat in enumerate(win.beats):
        if str(getattr(beat, 'spec_name', '') or ''):
            target = idx
            break
    print('target =', target, 'beats =', len(win.beats))
    if target is None:
        return 1
    win._show_index(target)
    app.processEvents()
    app.processEvents()
    OUT.mkdir(parents=True, exist_ok=True)
    png = OUT / 'card_from_ext_2026-11-07.png'
    win.grab().save(str(png))
    print('saved', png)

    from storygraph.app import beats as B
    from storygraph.story.statement_registry import StatementRegistry
    statements = StatementRegistry.for_project(str(root))
    cols, vals = B.spec_arg_fields_for(win.beats[target], statements)
    print('kinds  =', [(c['header'], c.get('kind')) for c in cols])
    print('values =', vals)
    win.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
