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
`python -m storygraph.main_qt` 和 `python storygraph/main_qt.py`
这种从包内启动的用法。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 允许 `python storygraph/main_qt.py` 直接跑：脚本方式下 `sys.path[0]`
# 是 `storygraph/` 而不是仓库根，`import storygraph...` 会直接
# ModuleNotFoundError。包方式（`python -m storygraph.main_qt`）不走这里。
if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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


#: `_guess_project_root` 的第二返回值：这个根是**怎么来的**。
#:
#: ⚠ 为什么必须区分（2026-10-13 实测）：命令行**明确**给的项目根是你的指定，
#: 不能被"上次那个"盖掉；而"从当前工作目录猜出来的"只是一个方便 ——
#: 它**不该**把会话记忆顶掉（双击 exe 时 cwd 常常正好是某个项目根，
#: 那会让"记忆"永远用不上）。
ROOT_FROM_ARG = 'arg'          # 命令行里明确给了（目录 / 章节所在项目）
ROOT_FROM_CWD = 'cwd'          # 从当前工作目录猜的（只是个方便）


def _guess_project_root(argv) -> tuple:
    """
    从参数里猜项目根与单章路径（.rpy → 向上找 game/ 的父目录）。

    都没有的话，**从当前工作目录向上找 `game/`** —— 用惯了
    `cd 项目根 && python main.py` 的人不该还要手点一次「打开项目」才能
    用上资源表（立绘 / 音乐候选、缩略图）。

    返回 `(root, script, source)`；`source` 是 `ROOT_FROM_ARG` / `ROOT_FROM_CWD`
    （区分见那两个常量的注释 —— 会话记忆要不要让位就靠它）。

    注意它**只影响资源侧**：剧情图任何时候都从空开始，只放用户导入的章节。
    """
    script = None
    for a in argv:
        if str(a).lower().endswith('.rpy'):
            script = str(a)
            break
    root, source = None, ''
    for a in argv:
        p = Path(a)
        if p.is_dir() and (p / 'game').is_dir():
            root, source = p, ROOT_FROM_ARG
            break
    if root is None and script:
        p = Path(script).resolve()
        for anc in p.parents:
            if (anc / 'game').is_dir():
                root, source = anc, ROOT_FROM_ARG
                break
    if root is None:
        for anc in [Path.cwd(), *Path.cwd().parents]:
            if (anc / 'game').is_dir():
                root, source = anc, ROOT_FROM_CWD
                break
    return root, script, source


def main(argv=None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:                          # noqa: BLE001
        print('PySide6 未安装：pip install PySide6')
        return 2

    root, script, source = _guess_project_root(argv)
    app = QApplication(sys.argv[:1])
    app.setApplicationName('剧情编辑器（Qt 版）')

    # ⚠ **退出前等后台线程收尾**（2026-10-24，实测到的 access violation）：
    #   `thumb_loader._schedule_cache_prune` 把缩略图缓存清理丢进
    #   `QThreadPool.globalInstance()`。窗口关掉之后那个线程可能还在跑，
    #   进程退出时 GC 撞上它就是 `access violation`（6 个窗口的测试组合
    #   稳定复现）。这里给最多 2 秒，超时也照常退出 —— 清理只是磁盘卫生。
    try:
        from PySide6.QtCore import QThreadPool
        app.aboutToQuit.connect(
            lambda: QThreadPool.globalInstance().waitForDone(2000))
    except Exception as e:                    # noqa: BLE001 - 清理失败不该拦启动
        from storygraph.core.safeguard import swallow
        swallow('main_qt.wait_threads_at_exit', e)

    from storygraph.core import session_store as SS
    from storygraph.ui_qt.main_window import open_editor

    # ⚠⚠ **"猜出来的项目根"要让位给记忆**（2026-10-13 实测修）。
    #
    # 双击 exe 时工作目录常常正好是某个项目根（或它下面），于是这里会猜出一个
    # 根 —— 那只是个方便，不是你的指定。以前把猜出来的根喂给窗口，就等于
    # 告诉窗口"命令行给过了"，会话记忆里那道门（`_boot_restore_session`）
    # 于是永远关上：**"下次自动回到上次的项目"在最常见的用法下彻底失效**。
    #
    # 所以：只有**命令行明确给的**根（`ROOT_FROM_ARG`）才算"指定"。
    # 猜出来的根在"记忆不可用"时（关掉了自动打开 / 设了 STORYGRAPH_NO_SESSION）
    # 才用得上 —— 那时没有记忆可让位。
    explicit = root if source == ROOT_FROM_ARG else None
    memory_on = SS.enabled() and not script
    if explicit is None and root is not None and memory_on:
        root = None                  # 交给记忆去决定开哪个项目

    # ── 演出专用版：随包项目 + 随包预设（2026-10-29）──────────────────
    #
    # 交付给文案/策划的包是"工具 + 完整项目文件夹"两份东西。他们没有
    # VSCode / Ren'Py / Python，也不该先学会"📁 打开项目要点到哪一层"：
    #   · `项目/` 放在工具旁边（或把项目文件夹拖到 exe 上）→ 自动打开；
    #   · 项目里缺 `storygraph.rules.json` / 布局 → 从随包预设补（只补缺）。
    # 标准版（不是演出专用版）这一整块**不执行**，行为与以前完全一样。
    from storygraph.app import engine_preview as _EP
    from storygraph.app import stage_edition as _SE
    stage = False
    received = None
    preset_notes: list = []
    try:
        stage = _EP.stage_edition()
    except Exception as e:                       # noqa: BLE001 - 判断不出就当标准版
        from storygraph.core.safeguard import swallow as _swallow
        _swallow('main_qt.stage_edition', e)
    if stage:
        if explicit is None and not script:
            try:
                received = _SE.find_received_project()
            except Exception as e:               # noqa: BLE001 - 找不到不是错误
                from storygraph.core.safeguard import swallow as _swallow
                _swallow('main_qt.received_project', e)
                received = None
            # 有"上次那个项目"时**让记忆优先**：它大概率就是这个随包项目，
            # 恢复会把上次那一章和版式一起摆回来；不是同一个项目时也不该
            # 被旁边放的文件夹悄悄顶掉。
            if received is not None and memory_on:
                try:
                    remembered = SS.last_project(SS.config_path())
                except Exception as e:           # noqa: BLE001 - 记忆读不出来就当没有
                    from storygraph.core.safeguard import swallow as _swallow
                    _swallow('main_qt.memory_last_project', e)
                    remembered = ''
                if remembered:
                    received = None
        for _candidate in (explicit, received):
            if _candidate is None:
                continue
            try:
                _presets = _SE.apply_presets(_candidate)
                preset_notes.extend(_presets.get('actions') or ())
                preset_notes.extend(_presets.get('errors') or ())
            except Exception as e:               # noqa: BLE001 - 补预设失败不拦启动
                from storygraph.core.safeguard import swallow as _swallow
                _swallow('main_qt.apply_presets', e)

    engine = _EngineShim(root)
    # graph 一律从**空**开始：图里只该出现用户导入过的章节。
    # （以前这里 `analyze(files=discover_scripts(root))` 一次把 40 个脚本
    #   灌进图里 → 51 个块，"看都要看瞎了"。）
    win = open_editor(engine, script, explicit, None)
    if explicit is not None:
        # 项目根只喂资源表（xlsx）：立绘 / 音乐 / 角色候选、缩略图路径。
        win.open_project_path(explicit)
    if received is not None:
        # 随包项目：打开资源侧（规则/候选/导航都会跟着就位）。
        win.open_project_path(received)
        try:
            win._log(f'[演出专用版] 已自动打开随包项目：{received}')
            win._status('已自动打开随包项目；左侧点一章开始'
                        '（卡片页工具栏有「🎮 引擎试玩」）')
        except Exception as e:                   # noqa: BLE001 - 写日志失败不拦
            from storygraph.core.safeguard import swallow as _swallow
            _swallow('main_qt.received_status', e)
    if preset_notes:
        try:
            for _note in preset_notes:
                win._log('[预设] ' + str(_note))
        except Exception as e:                   # noqa: BLE001 - 写日志失败不拦
            from storygraph.core.safeguard import swallow as _swallow
            _swallow('main_qt.preset_notes', e)
    if script:
        # 只把这一个章节放进剧情图（累加，不动别的）。
        win.import_chapter_files([script])
    if explicit or received is not None:
        return app.exec()
    # ⚠ 这里**不再提示"正在恢复"**（2026-10-13 用户要求）：
    #   恢复是**自动且无 UI** 的，控制台由 `window_state` 打一行
    #   "已自动读取项目：…" 就够了 —— 再多一条"正在恢复…"只是噪音。
    if root is None and not script and not memory_on:
        win._status('点「➕ 导入章节 .rpy」把要看的章节加进剧情图；'
                    '「📁 打开项目」只用于资源表（xlsx）')
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
