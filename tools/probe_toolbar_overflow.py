"""
测两页顶部工具栏的**溢出**：哪些动作在真实窗口宽度下会被折进「»」菜单。

    python tools/probe_toolbar_overflow.py [窗口宽度，默认 1500]

为什么需要它：`QToolBar` 装不下时不会报错，只是**把后面的按钮藏起来**。
"按钮存在"和"用户看得见"是两件事 —— 2026-10-21 用户说"导出太常用，
要放在最上面那一栏"，就是因为排在后面的动作等于不存在。

顺带在 `%TEMP%/sg_ui_shots/` 下留两张截图（结构页 / 卡片页）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication                      # noqa: E402

OUT = Path(os.environ.get('TEMP', '.')) / 'sg_ui_shots'
SCRIPT = '''\
label ch:
    scene bg room
    show eileen happy at center
    "第一句台词"
    "第二句台词"
    return
'''


def main() -> int:
    width = int(sys.argv[1]) if len(sys.argv) > 1 else 1500
    app = QApplication.instance() or QApplication([])
    from storygraph.ui_qt.main_window import MainWindowQt

    OUT.mkdir(parents=True, exist_ok=True)
    proj = OUT / 'proj'
    (proj / 'game').mkdir(parents=True, exist_ok=True)
    script = proj / 'game' / 'ch.rpy'
    script.write_text(SCRIPT, encoding='utf-8')

    class _Engine:
        project_root = str(proj)

        def get_registry(self):
            return None

    win = MainWindowQt(_Engine(), script)
    win.resize(width, 980)
    win.show()
    app.processEvents()

    for name, page in (('graph', win.PAGE_GRAPH), ('card', win.PAGE_CARD)):
        win._set_page(page)
        app.processEvents()
        win.grab().save(str(OUT / f'{name}.png'))
        tb = win._tb_graph if page == win.PAGE_GRAPH else win._tb_card
        need = tb.sizeHint().width()
        hidden = []
        for a in tb.actions():
            w = tb.widgetForAction(a)
            if w is None or not a.text().strip():
                continue
            if not w.isVisible():
                hidden.append(a.text())
        print(f'--- {name} 页：需要 {need}px / 实宽 {tb.width()}px，'
              f'折进「»」的 {len(hidden)} 个')
        for text in hidden:
            print(f'    溢出  {text}')
    print('截图：', OUT)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
