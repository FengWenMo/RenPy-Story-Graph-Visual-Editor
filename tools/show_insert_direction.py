"""
演示：两个插入按钮到底往哪儿插（点**真按钮**，打印卡片列表）。

点一次按钮、打印一次列表 —— 列表里 `▶` 标出"当前选中的那一张"，
新插的空卡显示成 `（新卡）`。

用法：python tools/show_insert_direction.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication            # noqa: E402

from _ui_project import build_project                  # noqa: E402
from storygraph.ui_qt.main_window import MainWindowQt  # noqa: E402


def _label(beat) -> str:
    text = (beat.text or beat.comment or beat.stage or beat.code
            or beat.raw or '')
    text = text.strip().replace('\n', ' ')[:28]
    return text or '（新卡/空）'


def show(win, title: str) -> None:
    cur = win._loaded_idx
    print(f'  {title}')
    for i, b in enumerate(win.beats):
        mark = '▶' if i == cur else ' '
        print(f'    {mark} {i + 1:>2}. {_label(b)}')
    print()


def main() -> int:
    app = QApplication.instance() or QApplication([])
    # `ignore_cleanup_errors`：窗口还开着的时候 Qt 可能仍持有工程里的 png，
    # 临时目录删不掉会抛 PermissionError（Windows）—— 那只是演示脚本的收尾，
    # 不该让它变成"退出码 1"。
    with tempfile.TemporaryDirectory(prefix='insert_demo_',
                                     ignore_cleanup_errors=True) as td:
        proj = build_project(Path(td) / 'proj')
        script = proj / 'game' / 'script' / 'ch01.rpy'

        class _Engine:
            project_root = proj

            def get_registry(self):
                return None

        win = MainWindowQt(_Engine(), script)
        win._set_page(win.PAGE_CARD)

        for name, btn in (('「＋ 插入卡片」', win._insert_btn),
                          ('「＋ 插到前面」', win._insert_before_btn)):
            win._show_index(2)                 # 每次都从"第 3 张"开始
            show(win, f'点 {name} 之前（当前选中 ▶ 第 3 张）：')
            btn.click()                        # ← 真按钮、真信号
            show(win, f'点 {name} 之后：')
        win.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
