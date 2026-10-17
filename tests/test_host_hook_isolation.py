"""
宿主钩子的**进程隔离**（2026-10-18）
====================================

上一轮只做到"默认拒绝"：授权之后项目代码仍在**主进程 `exec`**。项目代码
只要 `sys.exit()` / 死循环 / 打印几百 MB，编辑器就跟着完蛋。

这个文件守：
  ① 项目代码跑在**另一个 pid** 里；
  ② 死循环/睡觉 → 到点杀掉，调用方拿到 `None`（不是假装通过）；
  ③ 崩溃（`os._exit`）→ 只记一笔，工具照常返回；
  ④ 项目代码 `print` 洪水不污染 JSON 协议；超长响应被截断并杀掉子进程；
  ⑤ 声明抛异常不会连坐后面的验证调用；
  ⑥ 导入就崩 → `available=False` + 人话说明；`close()` 能收尸。
"""

from __future__ import annotations

import os
import sys
import textwrap
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.story import host_hooks as HH                    # noqa: E402
from storygraph.story.host_hooks import load_host_hooks          # noqa: E402


def _project(tmp_path, body: str):
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    (root / 'storygraph.host.py').write_text(textwrap.dedent(body),
                                             encoding='utf-8')
    return root


def _trusted(tmp_path, body: str):
    return load_host_hooks(_project(tmp_path, body), trust=True)


def test_hook_runs_in_a_separate_process(tmp_path):
    """项目代码必须跑在**另一个 pid** 里（不再 `exec` 到编辑器进程）。"""
    hooks = _trusted(tmp_path, '''
        import os

        def declarations():
            return [{'name': f'pid_{os.getpid()}'}]

        def validate_statement(head, text):
            return True, str(os.getpid())
    ''')
    try:
        rows = hooks.declarations()
        assert rows and rows[0]['name'].startswith('pid_'), rows
        child_pid = int(rows[0]['name'][4:])
        assert child_pid != os.getpid(), '项目代码还在主进程里跑'
        assert hooks.worker_pid == child_pid
        ok, why = hooks.validate('x', 'x')
        assert ok is True and why == str(child_pid)
    finally:
        hooks.close()


def test_validate_timeout_is_killed_and_reported(tmp_path, monkeypatch):
    """睡死 → 超时杀掉子进程，返回 `None`，notes 里说清超时。"""
    monkeypatch.setattr(HH, 'VALIDATE_TIMEOUT', 0.8)
    hooks = _trusted(tmp_path, '''
        import time

        def validate_statement(head, text):
            time.sleep(30)
            return True, 'never'
    ''')
    try:
        started = time.monotonic()
        ok, why = hooks.validate('slow', 'slow x')
        spent = time.monotonic() - started
        assert ok is None, '超时被当成了通过'
        assert spent < 10, f'超时没生效：{spent:.1f}s'
        assert '没有给出结论' in why, why
        assert any('没有响应' in n or '超时' in n for n in hooks.notes), hooks.notes
        assert hooks._worker.proc.poll() is not None, '超时后子进程还活着'
    finally:
        hooks.close()


def test_crash_does_not_take_the_tool_down(tmp_path):
    """项目代码 `os._exit()` → 工具不崩，后续调用立刻说"子进程已退出"。"""
    hooks = _trusted(tmp_path, '''
        import os

        def validate_statement(head, text):
            os._exit(3)
    ''')
    try:
        ok, why = hooks.validate('boom', 'boom x')
        assert ok is None
        assert '没有给出结论' in why, why
        assert any('exit=3' in n or '提前退出' in n for n in hooks.notes), hooks.notes
        # 第二次调用不再等超时（进程已经没了）
        started = time.monotonic()
        ok2, _ = hooks.validate('again', 'again x')
        assert ok2 is None
        assert time.monotonic() - started < 2
    finally:
        hooks.close()


def test_print_flood_does_not_break_the_protocol(tmp_path):
    """项目代码往 stdout 打东西（含 import 时）→ 协议照常工作。"""
    hooks = _trusted(tmp_path, '''
        print('import 时的一行')
        import sys
        print('stderr 也行', file=sys.stderr)

        def declarations():
            print('声明时又打了一行')
            return [{'name': 'weather'}]

        def validate_statement(head, text):
            print('x' * 20000)
            return True, 'ok'
    ''')
    try:
        assert hooks.declarations() == [{'name': 'weather'}]
        assert hooks.validate('weather', 'weather rain') == (True, 'ok')
    finally:
        hooks.close()


def test_oversize_response_is_capped(tmp_path, monkeypatch):
    """单行响应超过上限 → 杀掉子进程、记一笔，不把整个 JSON 读进内存。"""
    monkeypatch.setattr(HH, 'MAX_LINE_BYTES', 4096)
    hooks = _trusted(tmp_path, '''
        def declarations():
            return [{'name': 'x', 'blob': 'A' * 200000}]
    ''')
    try:
        assert hooks.declarations() == []
        assert any('8 MiB' in n or '超过' in n or '协议' in n for n in hooks.notes), \
            hooks.notes
    finally:
        hooks.close()


def test_one_bad_call_does_not_poison_the_next(tmp_path):
    """`declarations()` 抛异常不能连坐 `validate()`（同一个子进程还活着）。"""
    hooks = _trusted(tmp_path, '''
        def declarations():
            raise ValueError('声明炸了')

        def validate_statement(head, text):
            return True, '我还是好的'
    ''')
    try:
        assert hooks.declarations() == []
        assert any('声明炸了' in n for n in hooks.notes), hooks.notes
        assert hooks.validate('x', 'x') == (True, '我还是好的')
    finally:
        hooks.close()


def test_import_crash_is_reported_not_raised(tmp_path):
    """钩子导入就崩 → `available=False`、notes 有人话理由、调用不抛异常。"""
    hooks = _trusted(tmp_path, '''
        import os
        os._exit(7)
    ''')
    try:
        assert hooks.available is False
        assert any('没跑起来' in n or '加载失败' in n or 'exit=7' in n
                   for n in hooks.notes), hooks.notes
        assert hooks.declarations() == []
        assert hooks.validate('x', 'x') == (None, '')
    finally:
        hooks.close()


def test_close_reaps_the_process(tmp_path):
    hooks = _trusted(tmp_path, '''
        def declarations():
            return []
    ''')
    proc = hooks._worker.proc
    hooks.close()
    assert proc.poll() is not None, 'close() 之后子进程还活着'
