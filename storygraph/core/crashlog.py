"""
storygraph.core.crashlog
========================
**打包成 exe 之后，崩溃必须留下痕迹。**

为什么需要它（交付质检时发现的风险）：项目一直用 `python main.py` 启动 ——
控制台就在旁边，`traceback` 直接打在屏幕上。而一旦打成 Windows 的
**窗口程序（`--noconsole`）**，`stderr` 被接到了空设备上：

    · Qt 还没起来就崩（缺插件、DLL 没找到）→ 双击图标**什么都不发生**；
    · 界面起来了但某个槽函数抛异常 → 窗口卡住或直接消失。

用户看到的是"这东西坏了"，而我们**一行日志都没有** ——
这正是项目自己最反对的那类缺陷（契约 H1：静默失败比报错坏得多）。
`core/safeguard.py` 管的是"被吞掉的异常"，这里管的是**没人接的异常**。

做法（刻意极小，不碰任何业务逻辑）：
  · `install()` 挂 `sys.excepthook` 与 `threading.excepthook`，
    把 traceback **追加**写进一个日志文件，然后**照常交给原来的钩子**
    —— 不改变退出行为、不吞异常、不影响非冻结环境；
  · 日志位置：`%LOCALAPPDATA%/StoryGraph/crash.log`（Windows）/
    `~/.local/state/storygraph/crash.log`（其它），可用
    `STORYGRAPH_CRASH_LOG` 覆盖；都写不进去就退回临时目录；
  · `install()` 返回日志路径，入口把它显示出来 ——
    "崩了但不知道去哪看"等于没记。

由 `storygraph.main.main()` 在**冻结环境**（`sys.frozen`）里调用；
源码运行时不装（保持既有行为，测试也不受影响）。
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import traceback
from contextlib import suppress
from datetime import datetime
from pathlib import Path

__all__ = ['log_path', 'install', 'record']

#: 覆盖日志位置的入口（CI / 排障用）。
ENV_OVERRIDE = 'STORYGRAPH_CRASH_LOG'

#: 日志上限：超过就**轮转**（保留上一份 `.1`），不让它无限长大。
MAX_BYTES = 512 * 1024

_installed = False
_resolved: "Path | None" = None
#: `install()` 挂上去的那个后台线程钩子。
#:
#: 为什么留一个引用（而不是只塞进 `threading.excepthook`）：测试要验证
#: "线程里的异常真的会进日志"，而 `threading.excepthook` 是个**全局钩子**,
#: 会被别人（pytest 自己的 `threadexception` 插件、别的库）包一层 ——
#: 从外面调等于调它们那一层，测到的不是我们装的东西。
thread_excepthook = None


def _candidates() -> list:
    """候选日志位置，按优先级排（第一个能写的就是它）。"""
    out = []
    override = str(os.environ.get(ENV_OVERRIDE, '')).strip()
    if override:
        out.append(Path(override))
    if sys.platform == 'win32':
        base = os.environ.get('LOCALAPPDATA') or os.environ.get('APPDATA')
        if base:
            out.append(Path(base) / 'StoryGraph' / 'crash.log')
    else:
        xdg = os.environ.get('XDG_STATE_HOME')
        home = os.environ.get('HOME')
        if xdg:
            out.append(Path(xdg) / 'storygraph' / 'crash.log')
        elif home:
            out.append(Path(home) / '.local' / 'state' / 'storygraph'
                       / 'crash.log')
    # 最后退路：临时目录（几乎总可写）。exe 放在 Program Files 时，
    # 旁边那份写不进去 —— 没有退路就等于没日志。
    out.append(Path(tempfile.gettempdir()) / 'StoryGraph' / 'crash.log')
    return out


def log_path() -> Path:
    """当前选定的日志路径（第一次调用时确定，之后固定不变）。"""
    global _resolved
    if _resolved is not None:
        return _resolved
    for cand in _candidates():
        with suppress(OSError):
            cand.parent.mkdir(parents=True, exist_ok=True)
            # 真正验一次"可写"，别等到崩溃那一刻才发现不行。
            with cand.open('a', encoding='utf-8'):
                pass
            _resolved = cand
            return _resolved
    # 一个都写不进去：返回第一个候选，`record` 会自己 suppress 掉失败。
    _resolved = _candidates()[0]
    return _resolved


def _rotate(path: Path) -> None:
    """超过 `MAX_BYTES` 就把它挪成 `.1`（只留一代，够排障了）。"""
    with suppress(OSError):
        if path.exists() and path.stat().st_size > MAX_BYTES:
            backup = path.with_suffix(path.suffix + '.1')
            with suppress(OSError):
                backup.unlink()
            path.replace(backup)


def record(text: str) -> None:
    """
    追加一段文本到日志。**绝不抛** —— 它自己就在崩溃路径上。

    （用 `contextlib.suppress` 而不是 `except: pass`：后者是全项目
    唯一留给 `core.safeguard.swallow` 的写法，由
    `tests/test_no_silent_except.py` 守着。）
    """
    path = log_path()
    with suppress(OSError):
        _rotate(path)
        stamp = datetime.now().isoformat(timespec='seconds')
        with path.open('a', encoding='utf-8') as fh:
            fh.write(f'\n===== {stamp} =====\n{text}\n')


def _format(exc_type, exc, tb) -> str:
    head = ''.join(traceback.format_exception(exc_type, exc, tb))
    try:
        from storygraph.core import safeguard
        tail = safeguard.report()
    except Exception as e:                  # noqa: BLE001 - 报告拿不到也要写日志
        # 这里**不能**再调 `record`（就是它调过来的），留给 `swallow`。
        from storygraph.core.safeguard import swallow
        swallow('core.crashlog._format', e)
        tail = ''
    blob = head if not tail else f'{head}\n{tail}\n'
    return (f'{blob}--- 环境 ---\n'
            f'python {sys.version.split()[0]} / {sys.platform}\n'
            f'frozen {getattr(sys, "frozen", False)}\n'
            f'executable {sys.executable}\n'
            f'cwd {os.getcwd()}\n')


def _make_thread_hook(previous):
    """
    造一个后台线程钩子：**先记日志，再照原样交给 `previous`**。

    抽成"接收 previous"的形式（而不是在 `install` 里直接读全局）是为了能
    脱离进程里真实的那条链去测 —— 全局 `threading.excepthook` 会被别人
    （pytest 的 `threadexception` 插件、别的库）包一层，从外面调等于测它们的
    包装，而不是测我们装的这一个。
    """

    def _hook(args):
        with suppress(Exception):
            record('--- 后台线程未捕获异常 ---\n'
                   + _format(args.exc_type, args.exc_value,
                             args.exc_traceback))
        if previous is not None:
            with suppress(Exception):
                previous(args)

    return _hook


def install() -> Path:
    """
    挂上钩子，返回日志路径。重复调用只装一次。

    **原来的钩子照常调用**：这里不做"崩溃被接管"这种事 ——
    只加痕迹，不改行为。非法的 `sys.excepthook`（被换成了别的东西）
    也照原样链下去。
    """
    global _installed
    path = log_path()
    if _installed:
        return path
    _installed = True

    previous = sys.excepthook

    def _hook(exc_type, exc, tb):
        with suppress(Exception):
            record(_format(exc_type, exc, tb))
        if previous is not None:
            with suppress(Exception):
                previous(exc_type, exc, tb)

    sys.excepthook = _hook

    # 后台线程（缩略图加载、资源扫描）里的异常默认只打 stderr ——
    # 冻结成窗口程序后同样是"看不见"。
    hook = _make_thread_hook(getattr(threading, 'excepthook', None))
    with suppress(Exception):
        globals()['thread_excepthook'] = hook
        threading.excepthook = hook

    # 原生层崩溃（Qt/DLL）拿不到 Python traceback，但能留下"崩在哪"。
    #
    # ⚠ 必须交给 faulthandler 一个**独立**的文件描述符（`os.dup`）：
    # 直接把打开的文件对象给它，Windows 上这个句柄会一直握着 ——
    # 用户想看日志会发现文件被占用，而我们自己也没法再追加写。
    with suppress(Exception):
        import faulthandler
        fh = path.open('a', encoding='utf-8')
        faulthandler.enable(file=os.dup(fh.fileno()), all_threads=True)
        fh.close()

    return path
