"""
storygraph.main_qt
==================
Qt（PySide6）版桌面应用入口 —— **剧情结构图 + 卡片界面**。

    python main.py                       # 空图（自己「➕ 导入章节 .rpy」）
    python main.py 项目根                 # 只加载**资源表**（xlsx），图仍是空的
    python main.py 某章.rpy               # 把这一章导入剧情图并载入会话

**为什么"给项目根"不等于"把整部戏画出来"**（用户 2026-10-03 纠正）：

> 我没有叫你所有的文件全部导进剧情图内！……所谓的导入项目只是为了表格的
> xlsx 的资源管理系统！不是把整个项目都排在剧情图上面！！！

所以这里不再"扫全项目 → 建图"：项目根只喂给资源侧（立绘 / 音乐 / 角色
候选与缩略图），剧情图**只放用户自己导入的章节**。带 `.rpy` 参数时只导入
那一个章节。

它**不再是"另一个入口"**：`python main.py` 走的就是这里（tkinter 版
已于 2026-10-01 彻底移除）。保留本模块只为兼容
`python -m storygraph.main_qt` 这种老用法。
"""
from __future__ import annotations

import sys
from pathlib import Path

from storygraph.core.safeguard import swallow


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
            except Exception as e:                    # noqa: BLE001
                swallow('main_qt.get_registry', e)
                self._registry = None
        return self._registry


def _guess_project_root(argv) -> tuple:
    """
    从参数里猜项目根与单章路径（.rpy → 向上找 game/ 的父目录）。

    都没有的话，**从当前工作目录向上找 `game/`** —— 用惯了
    `cd 项目根 && python main.py` 的人不该还要手点一次「打开项目」才能
    用上资源表（立绘 / 音乐候选、缩略图）。

    注意它**只影响资源侧**：剧情图任何时候都从空开始，只放用户导入的章节。
    """
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
    if root is None:
        for anc in [Path.cwd(), *Path.cwd().parents]:
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
    # graph 一律从**空**开始：图里只该出现用户导入过的章节。
    # （以前这里 `analyze(files=discover_scripts(root))` 一次把 40 个脚本
    #   灌进图里 → 51 个块，"看都要看瞎了"。）
    win = open_editor(engine, script, root, None)
    if root is not None:
        # 项目根只喂资源表（xlsx）：立绘 / 音乐 / 角色候选、缩略图路径。
        win.open_project_path(root)
    if script:
        # 只把这一个章节放进剧情图（累加，不动别的）。
        win.import_chapter_files([script])
    if root is None and not script:
        win._status('点「➕ 导入章节 .rpy」把要看的章节加进剧情图；'
                    '「📁 打开项目」只用于资源表（xlsx）')
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
