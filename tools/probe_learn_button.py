"""复现："打开项目之后「🧩 自定义模块 / 语句」点了没反应"。

    python tools/probe_learn_button.py
"""

import os
import sys
import tempfile
import traceback
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

from PySide6.QtWidgets import QApplication                    # noqa: E402
from _ui_project import build_project                         # noqa: E402

td = Path(tempfile.mkdtemp(prefix='learnbtn_'))
proj = build_project(td / 'proj')
# 让项目里有"自定义写法"，这样按钮该有活干
(proj / 'game' / 'script' / 'ch99.rpy').write_text(
    'label ch99:\n    swim_suit lyj "泳装" intensity=3\n    return\n',
    encoding='utf-8')

app = QApplication.instance() or QApplication([])
from storygraph.ui_qt.main_window import MainWindowQt          # noqa: E402


class _Engine:
    def __init__(self, root):
        self.project_root = root
        from storygraph.pipeline.build import load_asset_registry
        self._registry = load_asset_registry(root)

    def get_registry(self):
        return self._registry


win = MainWindowQt(_Engine(proj), proj / 'game' / 'script' / 'ch01.rpy')
print('项目根        =', win._project_root)
print('有 learn 方法 =', hasattr(win, 'learn_custom_statement'))

acts = [a for a in win._tb_graph.actions() if a.text().strip()]
print('工具栏顺序    =', [a.text() for a in acts][:4])
target = next((a for a in acts if '自定义模块' in a.text()), None)
print('找到动作      =', target is not None,
      '| 可见 =', None if target is None else target.isVisible(),
      '| 可用 =', None if target is None else target.isEnabled())
print('按钮 widget   =', None if target is None else
      type(win._tb_graph.widgetForAction(target)).__name__)

# —— 真的点一下（`QTimer` 必须在**调用之前**排好，否则模态 exec() 一进去就卡住）——
from PySide6.QtCore import QTimer                              # noqa: E402
from PySide6.QtWidgets import QDialog                          # noqa: E402

calls = []
real = win.learn_custom_statement


def spy():
    calls.append('called')
    return real()


win.learn_custom_statement = spy
opened = []


def _close_dialogs():
    for w in app.topLevelWidgets():
        if isinstance(w, QDialog) and w.isVisible():
            opened.append(w.windowTitle())
            w.reject()


QTimer.singleShot(80, _close_dialogs)
try:
    target.trigger()
    print('trigger() 没抛异常；spy 被调到 =', bool(calls))
    print('弹出来的窗 =', opened)
except Exception:                                   # noqa: BLE001
    print('trigger() 抛异常：')
    traceback.print_exc()

print('\n报告文本（注册表那段）应该不再是报错：')
from storygraph.story import statement_registry as SR          # noqa: E402
reg = SR.StatementRegistry.for_project(proj)
print(' ', SR.format_registry_report(reg)[:120])
