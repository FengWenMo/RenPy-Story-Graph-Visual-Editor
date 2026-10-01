"""
storygraph.main_qt
==================
Qt（PySide6）版桌面应用入口 —— **块状编辑器 + 卡片界面**（两个都要）。

    python -m storygraph.main_qt                 # 空项目（自己点"打开项目"）
    python -m storygraph.main_qt 项目根           # 直接扫这个项目，建出块状图
    python -m storygraph.main_qt 某章.rpy        # 从章节文件反推项目根并载入

tkinter 版入口仍是 `python main.py --tk`（迁移期并存，见
`docs/前端迁移方案_qt.md` 第六条）。
"""
from __future__ import annotations

import sys
from pathlib import Path


class _EngineShim:
    """
    给编辑器一个 `get_registry() / project_root` 的最小引擎。

    真实应用里这里该接 `AssetEngine`（会解析全项目、合并脚本定义）。
    现在先走轻量路径：按需读资源表 —— 编辑器该有的行为不受影响
    （`rescan_project` 里同样会合并脚本声明）。
    """

    def __init__(self, project_root=None):
        self.project_root = project_root
        self._registry = None

    def get_registry(self):
        if self._registry is None and self.project_root:
            try:
                from storygraph.pipeline.build import load_asset_registry
                self._registry = load_asset_registry(self.project_root)
            except Exception:                    # noqa: BLE001
                self._registry = None
        return self._registry


def _guess_project_root(argv) -> tuple:
    """从参数里猜项目根与单章路径（.rpy → 向上找 game/ 的父目录）。"""
    script = None
    for a in argv:
        if str(a).lower().endswith('.rpy'):
            script = str(a)
            break
    root = None
    for a in argv:
        p = Path(a)
        if p.is_dir() and (p / 'game').is_dir():
            root = p
            break
    if root is None and script:
        p = Path(script).resolve()
        for anc in p.parents:
            if (anc / 'game').is_dir():
                root = anc
                break
    return root, script


def main(argv=None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:                          # noqa: BLE001
        print('PySide6 未安装：pip install PySide6')
        return 2

    root, script = _guess_project_root(argv)
    app = QApplication(sys.argv[:1])
    app.setApplicationName('剧情编辑器（Qt 版）')

    from storygraph.ui_qt.main_window import open_editor

    engine = _EngineShim(root)
    graph = None
    if root is not None:
        try:
            from storygraph.parsing.discover import discover_scripts
            from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
            from storygraph.story.beat_build import apply_script_definitions
            files = discover_scripts(root)
            analyzer = IncrementalRenPyAnalyzer()
            parse_result, graph = analyzer.analyze(files=files, graph=None)
            reg = engine.get_registry()
            if reg is not None:
                apply_script_definitions(reg, parse_result)
        except Exception as e:                   # noqa: BLE001
            print(f'[提示] 扫项目失败（{e}）—— 可用工具栏「打开项目」重试')

    win = open_editor(engine, script, root, graph)
    if root is None:
        win._status('点「📁 打开项目」选择项目根目录（含 game/），即建出块状图')
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
