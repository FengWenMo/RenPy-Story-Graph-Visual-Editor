"""截图：声明里是**一段代码**的格子，在卡片上长什么样（自适应）。

    python tools/_probe_code_field_card_shot.py

临时项目，不碰真工程。产物 `docs/shots/card_code_field_2026-11-07.png`。
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

RULES = {'statements': [
    {'name': 'mycue', 'label': '预约镜头', 'docs': '推到指定位置并过一次场',
     'pattern': [
         {'kind': 'keyword', 'text': 'mycue', 'column': '动作名'},
         {'kind': 'variable', 'column': '文字'},
         # ← 这一格是"一段代码"：卡片上必须是多行框（2026-11-07）
         {'kind': 'code', 'column': '片段'},
     ],
     'clauses': [
         {'keyword': 'with', 'name': '转场', 'kind': 'transition'},
     ],
     'body': {'allows': ['say', 'show']}},
]}

SCRIPT = '''\
label ch:
    "开场"
    mycue "闪电" "Dissolve(0.1, 0.2)" with Fade(0.2, 0, 0.2)
    "收尾"
'''


def main() -> int:
    app = QApplication.instance() or QApplication([])
    from storygraph.ui_qt.main_window import MainWindowQt

    td = Path(tempfile.mkdtemp(prefix='shot-code-'))
    root = td / 'proj'
    (root / 'game').mkdir(parents=True)
    (root / 'storygraph.rules.json').write_text(
        json.dumps(RULES, ensure_ascii=False, indent=2), encoding='utf-8')
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
    png = OUT / 'card_code_field_2026-11-07.png'
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
