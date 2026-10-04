"""
验一验那份报告的说法：「导出控制流」两个菜单项是不是真的全废了、`False` 从哪来。

做两件事（都点真 UI）：

  A. 按**现在代码里的写法**触发那两个菜单项（`lambda: …(wrapper=True/False)`）
     → 看文件对话框有没有弹、有没有多出叫 `False` 的文件、有没有 TypeError。
  B. 换成"被 Qt 的 bool 顶进第一个位置参数"那种接法（`export_control_flow(False)`
     / `(True)`，等价于 `menu.addAction(text, self.export_control_flow)`）
     → 看真症状是什么。
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

from PySide6.QtWidgets import QApplication, QFileDialog   # noqa: E402

from _ui_project import build_project                      # noqa: E402
from storygraph.ui_qt.main_window import MainWindowQt      # noqa: E402


def _window(proj: Path) -> MainWindowQt:
    class _Engine:
        project_root = proj

        def get_registry(self):
            return None

    return MainWindowQt(_Engine(), None)


def main() -> int:
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory(prefix='flow_probe_',
                                     ignore_cleanup_errors=True) as td:
        proj = build_project(Path(td) / 'proj')
        win = _window(proj)
        # 造一个小图：两个模块（控制流导出需要图非空）
        from storygraph.graph.models import Graph
        g = Graph()
        g.add_node('a', 0, 0)
        g.add_node('b', 900, 600)
        win._set_graph(g)

        cwd = Path.cwd()
        junk = cwd / 'False'

        def _mtime():
            try:
                return junk.stat().st_mtime
            except OSError:
                return None

        # ---------- A. 现在代码里的写法（关键字传参）----------
        calls = []
        out = Path(td) / 'flow.rpy'
        QFileDialog.getSaveFileName = staticmethod(
            lambda *a, **k: (calls.append(1), (str(out), "Ren'Py (*.rpy)"))[1])
        before = _mtime()
        for w_ in (True, False):
            n0 = len(calls)
            try:
                win.export_control_flow(wrapper=w_)   # ← 菜单项就是这么调的
                r = ('弹了' if len(calls) > n0 else '**没弹对话框**')
                print(f'A `export_control_flow(wrapper={w_})` → 对话框{r}；'
                      f'产物 {"有" if out.exists() else "没有"}'
                      f'（cwd 里的 False 文件{mtime_note(before, _mtime())}）')
            except Exception as e:                    # noqa: BLE001
                print(f'A `export_control_flow(wrapper={w_})` 抛异常：'
                      f'{type(e).__name__}: {e}')

        # ---------- B. 被 Qt 的 bool 顶进第一个位置参数 ----------
        for bad in (False, True):
            calls.clear()
            out2 = Path(td) / f'flow_{bad}.rpy'
            QFileDialog.getSaveFileName = staticmethod(
                lambda *a, **k: (calls.append(1),
                                 (str(out2), "Ren'Py (*.rpy)"))[1])
            before = _mtime()
            print(f'\nB `export_control_flow({bad!r})`'
                  f'（= `clicked.connect(self.export_control_flow)` 的等价物）：')
            try:
                res = win.export_control_flow(bad)
                print(f'     没抛异常；对话框'
                      f'{"弹了" if calls else "**没弹（被绕过）**"}；'
                      f'返回 {res!r}')
            except Exception as e:                    # noqa: BLE001
                print(f'     ✗ 抛异常：{type(e).__name__}: {e}')
            note = mtime_note(before, _mtime())
            print(f'     cwd 里的 `False` 文件{note}')
        win.close()
    return 0


def mtime_note(before, after) -> str:
    if after is None:
        return "**不存在**"
    if before is None:
        return "**刚被创建** ← 就是它写出来的"
    if after != before:
        return "**刚被重写** ← 就是它写出来的"
    return "没动"


if __name__ == '__main__':
    raise SystemExit(main())
