"""
storygraph.main
===============
桌面应用入口 —— **PySide6（Qt）版**，tkinter 版已于 2026-10-01 彻底移除。

运行方式（三种等价，逻辑只有这一份）：
    python main.py                 # 启动 Qt 版编辑器
    python -m storygraph.main      # 同上
    python cli.py <子命令>          # 无界面，纯命令行（CI / 批处理用）

为什么 tkinter 版被删掉
-----------------------
它曾经是唯一入口，后来 Qt 版做出来却只挂在 `python -m storygraph.main_qt`
上，而用户平时用 `main.py` 启动 —— 于是看到的还是老界面（用户 2026-09-30：
"我这里显示完全没用到"）。两套前端并存期间，编辑语义一度**各写一套**
（Qt 版要伸手去摸 `self._panel._speaker` 这种私有属性）。

现在语义全部住在框架无关的 `storygraph.app.*` 里，前端只剩 Qt 一套，
`storygraph/ui/` 整个包已删除 —— 仓库里不可能再有第二种实现漂移。

PySide6 没装怎么办
------------------
**明确失败**，不再悄悄退回 tkinter（那条退路已经不存在了）。
报错里直接给出可执行的安装命令。
"""

from __future__ import annotations

import sys

from storygraph.core.safeguard import swallow


def _install_crash_log():
    """
    打包成 exe（`sys.frozen`）时把日志挂上 —— 见 `core.crashlog`。

    **只在冻结环境装**：源码运行时控制台就在旁边，traceback 直接看得见，
    再往磁盘写一份只会让"测试跑一遍"多出文件（老行为保持不动）。
    """
    if not getattr(sys, 'frozen', False):
        return None
    try:
        from storygraph.core import crashlog
        return crashlog.install()
    except Exception as e:                  # noqa: BLE001 - 日志装不上也要能开界面
        # 这里**没有别的退路**（要记日志的模块自己挂了），但必须留痕：
        # `safeguard` 的最小实现只依赖标准库，几乎不可能跟着一起挂。
        swallow('main._install_crash_log', e)
        return None


def main(argv=None) -> int:
    """启动图形界面，返回进程退出码。"""
    argv = list(argv if argv is not None else sys.argv[1:])
    # 先配好输出编码：打包成 exe 后 Windows 控制台是 GBK，而下面的提示语
    # 里有 `→` 之类的字符 —— 报错路径**不应该自己先崩**（见 core.consoleio）。
    # 窗口程序里 stdout 可能是 None，configure() 自己 suppress 掉。
    from storygraph.core import consoleio
    consoleio.configure()
    log = _install_crash_log()

    # `--selfcheck`：**打包后的自检**（见 storygraph.selfcheck）。
    # 必须排在开窗之前 —— 它要能在"界面根本起不来"的情况下也把结论说出来。
    # 图形版是窗口程序（没有控制台），所以结论同时写进崩溃日志。
    if '--selfcheck' in argv:
        from storygraph import selfcheck
        code = selfcheck.main()
        if log and code:
            print(f'      自检失败，详情见崩溃日志：{log}', file=sys.stderr)
        return code

    try:
        import PySide6                              # noqa: F401
    except ImportError as e:
        # 没有退路可退了，所以这里必须**把话说满**：装什么、怎么装。
        print(f'[错误] 界面需要 PySide6，但没有装（{e}）。', file=sys.stderr)
        print('       安装：  pip install PySide6', file=sys.stderr)
        print('       （只想跑无界面流程可以用：python cli.py validate <项目根>）',
              file=sys.stderr)
        if log:
            print(f'       崩溃日志：{log}', file=sys.stderr)
        return 1

    # PySide6 在就开 Qt 版；Qt 前端**自身的异常不吞** ——
    # 吞掉会变成"窗口没出来但退出码是 0"，比崩溃更难查。
    from storygraph.main_qt import main as qt_main
    code = qt_main(argv)
    # 只有"界面根本没起来"时提示日志位置，正常退出（含用户关窗）不打扰。
    if code not in (0, None) and log:
        print(f'[提示] 这次启动异常退出（码 {code}），崩溃日志：{log}',
              file=sys.stderr)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
