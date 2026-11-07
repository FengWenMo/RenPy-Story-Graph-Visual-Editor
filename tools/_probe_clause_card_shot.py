"""截图：**声明了子句的自定义语句**在卡片编辑器里到底能不能看见/编辑。

    python tools/_probe_clause_card_shot.py

造临时项目，不碰真项目。产物 `tools/out_custom_audit/clauses/*.png`。

要回答的问题（用户 2026-11-07）：
  "理论上完全能支持这样的源代码自定义……可是 UI 界面根本显示不出来！"
  —— `clauses`（`at` / `behind` / `with` / `zorder`）在声明、读值、折回里都有，
     卡片上到底有没有那一格？
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

OUT = ROOT / 'tools' / 'out_custom_audit' / 'clauses'

RULES = {'statements': [
    {
        'name': 'mycue', 'label': '预约镜头', 'block': True,
        'pattern': [
            {'kind': 'keyword', 'text': 'mycue', 'column': '动作名'},
            {'kind': 'variable', 'column': '文字'},
        ],
        'clauses': [
            {'keyword': 'at', 'name': '位置', 'label': '位置',
             'kind': 'words'},
            {'keyword': 'behind', 'name': '挡在', 'label': '挡在谁前面',
             'kind': 'word'},
            {'keyword': 'with', 'name': '转场', 'label': '过场',
             'kind': 'transition'},
            {'keyword': 'zorder', 'name': '层级', 'label': '层级',
             'kind': 'number'},
        ],
        'body': {'allows': ['say', 'show']},
    },
]}

SCRIPT = '''\
label ch:
    "开场"
    mycue "闪电" at 左 右 behind ye with Dissolve(0.3) zorder 5
    show lan happy at left behind ye with Dissolve(0.3) zorder 5
    "收尾"
'''


def main() -> int:
    app = QApplication.instance() or QApplication([])
    from storygraph.ui_qt.main_window import MainWindowQt

    td = Path(tempfile.mkdtemp(prefix='shot-clause-'))
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
    OUT.mkdir(parents=True, exist_ok=True)

    for name, idx in {'01_mycue': 1, '02_show': 2}.items():
        win._show_index(idx)
        app.processEvents()
        app.processEvents()
        png = OUT / f'{name}.png'
        win.grab().save(str(png))
        print('saved', png)

    # 打出卡片到底拿到了哪些"参数列"——这是判据，不是猜。
    # ⚠ 必须用**真项目那条路**拿注册表（`window_card._sync_spec_args` 就是这么做的），
    #   传 None 会得到空列，那会把"探针传错了"误读成"界面不支持"。
    try:
        from storygraph.app import beats as B
        from storygraph.story.statement_registry import StatementRegistry
        statements = StatementRegistry.for_project(str(root))
        print('registry statements =',
              [s.name for s in (getattr(statements, 'statements', None) or ())])
        for idx, b in enumerate(win.beats):
            cols, vals = B.spec_arg_fields_for(b, statements)
            head = str(getattr(b, 'spec_name', '') or '')
            print(f'--- beat[{idx}] spec_name={head!r} '
                  f'kind={str(getattr(b, "kind", ""))!r}')
            print('    columns =', [c.get('header') for c in cols])
            print('    values  =', vals)
            print('    opaque  =', str(getattr(b, 'opaque', '') or '')[:80])
            if head:
                raw = B.spec_arg_columns_of(b, statements)
                print('    raw cols=', [(c.get('header'), c.get('clause')) for c in raw])
    except Exception as e:                          # noqa: BLE001
        import traceback
        traceback.print_exc()

    win.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
