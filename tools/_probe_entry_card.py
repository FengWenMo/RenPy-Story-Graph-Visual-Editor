# -*- coding: utf-8 -*-
"""
临时探针：**跨章继承接进卡片**之后长什么样（offscreen 真窗口，只读）。

真机 `sr_general` / chapter1_2：这一章第 1 拍**没写 scene**（上一章留了 `bg9_2`）。
接线前卡片说"背景：（未设置）"；接线后应当：

  · 「当前舞台」第一行写「跨章继承：背景（本章没写过，来自上一章）」+ 为什么；
  · 「尺寸」一格出现背景行，出处 `source == -1`（按「改」→ 本章第一拍补一条 show）。

输出 `tools/_entry_card.png`（卡片右栏）与终端里的几行事实。
"""
import os
import tempfile
import time
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')
os.environ['STORYGRAPH_NO_SESSION'] = '1'
os.environ['STORYGRAPH_AUTOSAVE_DIR'] = tempfile.mkdtemp()

from PySide6.QtWidgets import QApplication                   # noqa: E402

REAL = Path(os.environ.get('STORYGRAPH_REAL_PROJECT')
            or r'D:\千连万理\sr_general')
TARGET = REAL / 'game' / 'chapter' / 'chapter1_2.rpy'

from storygraph.ui_qt.main_window import MainWindowQt        # noqa: E402
from storygraph.pipeline.build import load_asset_registry    # noqa: E402


class _Engine:
    def __init__(self, project_root):
        self.project_root = project_root
        self._registry = load_asset_registry(project_root)

    def get_registry(self):
        return self._registry


app = QApplication.instance() or QApplication([])
win = MainWindowQt(_Engine(REAL), TARGET)
win._set_page(win.PAGE_CARD)
win.resize(1280, 900)
win.show()
for _ in range(20):
    app.processEvents()
win._show_index(0)
for _ in range(30):
    app.processEvents()

print('=== 等后台分析（整本约 2 秒）===')
deadline = time.time() + 30
while time.time() < deadline:
    app.processEvents()
    if win._session.stage_initial_state() is not None:
        break
    time.sleep(0.05)
for _ in range(20):
    app.processEvents()

print('入口状态：', win._session.stage_initial_state() is not None)
print('入口说明：', win._session.stage_entry_note() or '（无）')
print('--- 当前舞台 ---')
print(win._session.stage_digest_text(0))
print('--- 尺寸行 ---')
for row in win._session.stage_size_rows(0):
    print(f"  {row['kind']}/{row['tag'] or '-'} spec={row['spec']!r} "
          f"source={row['source']} at={row['at']!r}")
    print('    note:', str(row.get('note') or '')[:80])
    print('    why :', str(row.get('why') or '')[:120])

out = Path('tools/_entry_card.png')
win._panel.grab().save(str(out))
win.hide()
print('saved', out)
