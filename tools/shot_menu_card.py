"""截图：带名 / 带参数的 `menu` 拍在卡片上长什么样（2026-10-19，P1-3 收尾）。

    python tools/_shot_menu_card.py

产物：`tools/out_custom_audit/menu9/*.png`。造的是**临时项目**，不碰真项目。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication                       # noqa: E402

OUT = ROOT / 'tools' / 'out_custom_audit' / 'menu9'

SCRIPT = '''\
label ch:
    "开场一句话"
    menu demo_menu (nvl=True):
        "选项 A":
            "选了 A"
        "选项 B" if flag:
            jump done
    return

label done:
    return
'''


def main() -> int:
    app = QApplication.instance() or QApplication([])
    from storygraph.ui_qt.main_window import MainWindowQt

    td = Path(tempfile.mkdtemp(prefix='shot-menu9-'))
    root = td / 'proj'
    (root / 'game').mkdir(parents=True)
    script = root / 'game' / 'ch.rpy'
    script.write_text(SCRIPT, encoding='utf-8')

    class _Engine:
        project_root = str(root)

        def get_registry(self):
            return None

    win = MainWindowQt(_Engine(), script)
    win.resize(1500, 980)
    win.show()
    app.processEvents()
    win._set_page(win.PAGE_CARD)
    OUT.mkdir(parents=True, exist_ok=True)

    beats = list(win.beats)
    menu_idx = next(i for i, b in enumerate(beats) if b.kind == 'menu')
    opt_idx = next(i for i, b in enumerate(beats) if b.kind == 'menu_option')
    print('菜单拍 idx=', menu_idx, '选项拍 idx=', opt_idx, '共', len(beats), '拍')

    # 菜单拍：折叠态（看标题与类型）
    win._list.setCurrentRow(menu_idx)
    app.processEvents()
    win.grab().save(str(OUT / '01_menu_collapsed.png'))
    # 菜单拍：展开高级区（`MenuName` / `MenuArgs` 两个输入框就在这里）
    win._panel._toggle_adv()
    app.processEvents()
    win.grab().save(str(OUT / '02_menu_advanced.png'))
    # 选项拍：证明选项是真的拍（可编辑），不是一堆原样块
    win._list.setCurrentRow(opt_idx)
    app.processEvents()
    win.grab().save(str(OUT / '03_menu_option.png'))
    print('截图 ->', OUT)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
