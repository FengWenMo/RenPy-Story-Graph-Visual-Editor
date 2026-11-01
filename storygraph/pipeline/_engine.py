"""
引擎子进程的最底层：SDK 在哪、用哪个 python 跑、怎么跑。

这个模块**只依赖标准库** —— 它存在的唯一理由是打断一条导入环：
`statement_probe` 要用 `find_sdk` / `_engine_python` / `run_engine`，
但 `engine_collect` 自己又要（延迟）import `statement_probe` 去问引擎结论
（`_attach_engine_verdicts`）。两个人互相 import，环就从这里长出来。

把三件"跑引擎"的事下沉成叶子模块，两边都往下看，环就没了：
`engine_collect` 与 `statement_probe` 都从这里取（前者继续 re-export，
既有调用方 / 测试不用改）。

⚠ 下沉带来的**副作用**（2026-11-01，同一个坑第三次踩）：这三个函数现在读的是
**本模块**的 `DEFAULT_SDK_CANDIDATES` / `SDK_ENV`。`monkeypatch.setattr(EC,
'DEFAULT_SDK_CANDIDATES', ())` 之后再调 `EC.find_sdk()` 是**打不中的** ——
`EC` 上那个名字只是转发来的值，函数体里看的是 `_engine` 的全局。
打桩要打在 `_engine` 上（`tests/test_engine_facts.py` 已改）。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

#: SDK 位置（换机器时指过来）。
SDK_ENV = 'STORYGRAPH_RENPY_SDK'

DEFAULT_SDK_CANDIDATES = (
    r'D:/software/renpy-8.4.1-sdk',
    r'C:/renpy',
)

DEFAULT_TIMEOUT = 600


def find_sdk(explicit=None) -> Path:
    """
    SDK 位置：显式参数 > 环境变量 > 候选列表。**找不到就抛**（不猜）。
    """
    cands = [explicit, os.environ.get(SDK_ENV, ''),
             *DEFAULT_SDK_CANDIDATES]
    for c in cands:
        if not c:
            continue
        p = Path(c)
        if (p / 'renpy.py').is_file():
            return p
    raise FileNotFoundError(
        f'找不到 Ren\'Py SDK（想找 renpy.py）。用 --sdk 或环境变量 {SDK_ENV} 指定')


def _engine_python(sdk: Path) -> Path:
    """SDK 自带的 python（拿不到就退回当前解释器）。"""
    for rel in ('lib/py3-windows-x86_64/python.exe',
                'lib/py3-linux-x86_64/python'):
        p = Path(sdk) / rel
        if p.is_file():
            return p
    return Path(sys.executable)


def run_engine(sdk, project_root, args, timeout=DEFAULT_TIMEOUT) -> tuple:
    """
    跑一次引擎命令 → `(退出码, 合并输出)`。环境里带上 `SDL_* = dummy`（无头）。

    ⚠ 超时**不抛**：返回 `(-1, 说明)`。采集是"可失败的动作"，不该把调用方炸掉。
    """
    cmd = [str(_engine_python(Path(sdk))), str(Path(sdk) / 'renpy.py'),
           str(project_root)] + [str(a) for a in args]
    env = dict(os.environ)
    env['SDL_VIDEODRIVER'] = 'dummy'
    env['SDL_AUDIODRIVER'] = 'dummy'
    try:
        p = subprocess.run(cmd, cwd=str(sdk), env=env, capture_output=True,
                           text=True, errors='replace', timeout=timeout)
    except subprocess.TimeoutExpired:
        return -1, f'引擎命令超时（>{timeout}s）：{" ".join(cmd[3:])}'
    except OSError as e:
        return -1, f'引擎命令起不来：{e!r}'
    return p.returncode, (p.stdout or '') + (p.stderr or '')


__all__ = ['SDK_ENV', 'DEFAULT_SDK_CANDIDATES', 'DEFAULT_TIMEOUT',
           'find_sdk', '_engine_python', 'run_engine']
