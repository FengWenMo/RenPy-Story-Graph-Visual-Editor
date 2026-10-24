# -*- coding: utf-8 -*-
"""
直接 `python storygraph/main_qt.py` 也必须能跑（2026-10-24 实测崩溃）。

脚本方式下 `sys.path[0]` 是 `storygraph/`，不是仓库根；入口文件里第一句
`from storygraph.core.safeguard import swallow` 于是直接
`ModuleNotFoundError: No module named 'storygraph'`。

这里用 `runpy.run_path(..., run_name='__entry_probe__')` 只执行模块顶层
（不会真的启动 Qt 窗口），并把 cwd 放在一个空的临时目录里 —— 如果入口
没有自己补包根，子进程会复现那个 ModuleNotFoundError。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _run_entry(rel: str, cwd: Path) -> subprocess.CompletedProcess:
    target = ROOT / rel
    code = ("import runpy\n"
            f"runpy.run_path({str(target)!r}, run_name='__entry_probe__')\n")
    return subprocess.run(
        [sys.executable, '-c', code],
        cwd=str(cwd), capture_output=True, text=True, timeout=60)


@pytest.mark.parametrize('rel', [
    'storygraph/main_qt.py',
    'storygraph/main.py',
    'main.py',
])
def test_entry_can_be_run_as_a_plain_script(rel, tmp_path):
    result = _run_entry(rel, tmp_path)
    assert result.returncode == 0, (
        f'{rel} 直接运行时在导入阶段就失败了：\n{result.stderr}')
