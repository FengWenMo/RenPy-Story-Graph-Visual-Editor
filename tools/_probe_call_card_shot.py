"""截图：`$ renpy.transition(Dissolve(0.1), layer="master")` 在卡片上能被编辑吗？

    python tools/_probe_call_card_shot.py

临时项目，不碰真工程。产物 `tools/out_custom_audit/calls/*.png`。

要回答的（用户 2026-11-07）：
  "你根本没办法让我把 $ renpy.transition(...) 当自定义写法输入进去，
   并自由决定它是否使用、是否能够批处理。"
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

OUT = ROOT / 'tools' / 'out_custom_audit' / 'calls'

SCRIPT = '''\
label ch:
    "开场"
    show lan happy
    $ renpy.transition(Dissolve(0.1), layer="master")
    "下一句"
    $ renpy.transition(Fade(0.2, 0, 0.2), layer="screens")
    "收尾"
'''


def main() -> int:
    app = QApplication.instance() or QApplication([])
    from storygraph.ui_qt.main_window import MainWindowQt

    td = Path(tempfile.mkdtemp(prefix='shot-call-'))
    root = td / 'proj'
    (root / 'game').mkdir(parents=True)
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

    for idx, beat in enumerate(win.beats):
        print(f'beat[{idx}] kind={getattr(beat, "kind", "")!r} '
              f'code={getattr(beat, "code", "")!r} '
              f'spec_name={getattr(beat, "spec_name", "")!r}')

    # 找到带 code 的那一拍
    target = None
    for idx, beat in enumerate(win.beats):
        if str(getattr(beat, 'code', '') or '').strip():
            target = idx
            break
    print('target =', target)

    if target is not None:
        win._show_index(target)
        app.processEvents()
        app.processEvents()
        png = OUT / '01_call_card.png'
        win.grab().save(str(png))
        print('saved', png)

        from storygraph.app import beats as B
        from storygraph.story.statement_registry import StatementRegistry
        statements = StatementRegistry.for_project(str(root))
        b = win.beats[target]
        cols, vals = B.spec_arg_fields_for(b, statements)
        print('columns =', [c.get('header') for c in cols])
        print('values  =', vals)

    win.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
