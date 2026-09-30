"""
storygraph.main_qt
==================
Qt（PySide6）版桌面应用入口。

  · 默认打开**剧本表编辑器**（Qt 版，重设计布局）；
  · 若带参数（一章 .rpy 的路径），直接载入它。

tkinter 版入口仍是 `main.py`（`storygraph.main`）——
迁移期间两个入口并存，逐窗口替换（见 `docs/前端迁移方案_qt.md` 第六条）。

    python -m storygraph.main_qt [章节.rpy]
"""
from __future__ import annotations

import sys


class _EngineShim:
    """
    给编辑器一个 `get_registry() / project_root` 的最小引擎。

    真实应用里这里该接 `AssetEngine`（会解析全项目、合并脚本定义）。
    现在先走轻量路径：按需读资源表 —— 编辑器该有的行为不受影响，
    只是"脚本里新写的 image/transform 声明"要**先过一次项目体检/导出**
    才会并进表里（与 tkinter 版在没有 AssetEngine 时的降级一致）。
    """

    def __init__(self, project_root=None):
        self.project_root = project_root
        self._registry = None

    def get_registry(self):
        if self._registry is None and self.project_root:
            try:
                from storygraph.pipeline.build import load_asset_registry
                from storygraph.parsing.discover import discover_scripts
                from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
                from storygraph.story.beat_build import apply_script_definitions
                reg = load_asset_registry(self.project_root)
                try:
                    az = IncrementalRenPyAnalyzer()
                    pr, _g = az.analyze(files=discover_scripts(self.project_root),
                                        graph=None)
                    apply_script_definitions(reg, pr)
                except Exception:                # noqa: BLE001
                    pass
                self._registry = reg
            except Exception:                    # noqa: BLE001
                self._registry = None
        return self._registry


def main(argv=None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:                         # noqa: BLE001
        print('PySide6 未安装：pip install PySide6')
        return 2
    from pathlib import Path

    script = None
    for a in argv:
        if a.endswith('.rpy'):
            script = a
            break
    project_root = None
    if script:
        p = Path(script).resolve()
        # 从 .../game/chapter/x.rpy 推项目根（向上找 game/ 的父目录）
        for anc in p.parents:
            if (anc / 'game').is_dir():
                project_root = anc
                break
    app = QApplication(sys.argv[:1] + ['-platform', 'windows']
                       if sys.platform.startswith('win') else sys.argv[:1])
    app.setApplicationName('剧本表编辑器（Qt 版）')

    from storygraph.ui_qt.main_window import open_editor
    open_editor(_EngineShim(project_root), script)
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
