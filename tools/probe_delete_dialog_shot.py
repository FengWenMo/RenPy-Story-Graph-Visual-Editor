"""截图：删一拍时确认框里到底写了什么（真机 pomodorro 的 breakend.0027）。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')
ROOT = Path(r'D:\千连万理\Renpy_Story_Graph_Editor')
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication, QMessageBox         # noqa: E402

from storygraph.ui_qt import window_card as WC                  # noqa: E402
from storygraph.ui_qt.main_window import MainWindowQt          # noqa: E402

OUT = Path(os.environ['TEMP']) / 'sg_ui_shots'
PROJ = Path(r'D:\千连万理\RenPyTest\sr_general')
SRC = PROJ / 'game' / 'pomodorro' / 'pomodorro.rpy'


def main() -> int:
    app = QApplication.instance() or QApplication([])
    OUT.mkdir(parents=True, exist_ok=True)

    class _Engine:
        project_root = str(PROJ)

        def get_registry(self):
            return None

    win = MainWindowQt(_Engine(), SRC)
    win.resize(1500, 980)
    win.show()
    app.processEvents()

    seen = []

    def _capture(parent, title, text, *a, **k):
        seen.append((title, str(text)))
        return QMessageBox.No

    WC.QMessageBox.question = staticmethod(_capture)
    win._set_page(win.PAGE_CARD)
    victim = next(i for i, b in enumerate(win._session.beats)
                  if (getattr(b, 'line_id', '') or '') == 'breakend.0027')
    win._show_index(victim)
    app.processEvents()
    win.grab().save(str(OUT / 'card_with_lines_hint.png'))
    win.delete_card()
    WC.QMessageBox.question = QMessageBox.question

    title, text = seen[-1]
    box = QMessageBox()
    box.setWindowTitle(title)
    box.setIcon(QMessageBox.Question)
    box.setText(text)
    box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
    box.button(QMessageBox.Yes).setText('删除')
    box.button(QMessageBox.No).setText('取消')
    box.show()
    app.processEvents()
    box.grab().save(str(OUT / 'delete_confirm.png'))
    print('--- 确认框原文 ---')
    print(text)
    win.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
