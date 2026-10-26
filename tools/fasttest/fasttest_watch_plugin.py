# -*- coding: utf-8 -*-
"""fasttest_watch_plugin —— 把每个 worker 当前在跑的 nodeid 写到文件里。

为什么需要它：全量测试卡住时，pytest 只显示 `[ 98%]`，人/agent 不知道
是哪一条用例把 worker 钉住了。`py-spy dump` 能看，但要求额外装工具；
这个插件只用标准库，每条用例开始/结束时各写一个小 JSON，卡死看门狗
（`run.py` 的 `--idle-timeout`）就能在结论里直接点名。

约定：
  · xdist worker 写 `<FASTTEST_WATCH_DIR>/gw0.json`；
  · 串行模式写 `main.json`；
  · xdist master 不写（它不执行用例，写了只会和 worker 抢同一个文件）。
  · 文件内容：`{"nodeid": "...", "started": <epoch>, "pid": 123}`。
  · 正常情况下 `logfinish` 会删掉；worker 崩溃时残留文件正好是线索。

这个插件**不影响测试结果**：任何写盘失败都静默跳过。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

#: 当前进程的 pytest Config；`pytest_configure` 里赋值，供没有 config 参数
#: 的 runtest hook 使用。每个 worker 是独立进程，互不影响。
_CONFIG = None


def _watch_dir(config) -> Path | None:
    raw = os.environ.get('FASTTEST_WATCH_DIR')
    if not raw:
        return None
    return Path(raw)


def _is_xdist_master(config) -> bool:
    """xdist master 有 -n 但自己没有 workerinput；worker 才有 workerinput。"""
    if hasattr(config, 'workerinput'):
        return False
    try:
        return bool(getattr(config.option, 'numprocesses', None))
    except (AttributeError, TypeError, ValueError):
        return False


def _path(config) -> Path | None:
    base = getattr(config, '_fasttest_watch_dir', None)
    if base is None:
        return None
    worker = getattr(config, '_fasttest_watch_worker', 'main') or 'main'
    safe = ''.join(ch if ch.isalnum() or ch in '_-' else '_'
                   for ch in worker)
    return Path(base) / f'{safe}.json'


def _write(path: Path, nodeid: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f'{path.name}.{os.getpid()}.tmp')
        tmp.write_text(json.dumps({
            'nodeid': nodeid,
            'started': time.time(),
            'pid': os.getpid(),
        }, ensure_ascii=False), encoding='utf-8')
        os.replace(tmp, path)
    except OSError:
        pass


def _clear(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def pytest_configure(config):
    global _CONFIG
    _CONFIG = config
    watch = _watch_dir(config)
    config._fasttest_watch_dir = watch
    config._fasttest_watch_worker = (
        os.environ.get('PYTEST_XDIST_WORKER') or 'main')
    config._fasttest_watch_master = _is_xdist_master(config)


def pytest_runtest_logstart(nodeid, location):
    config = _current_config()
    if config is None or getattr(config, '_fasttest_watch_master', False):
        return
    path = _path(config)
    if path is not None:
        _write(path, nodeid)


def pytest_runtest_logfinish(nodeid, location):
    config = _current_config()
    if config is None or getattr(config, '_fasttest_watch_master', False):
        return
    path = _path(config)
    if path is not None:
        _clear(path)


def pytest_sessionfinish(session, exitstatus):
    config = getattr(session, 'config', None)
    if config is None or getattr(config, '_fasttest_watch_master', False):
        return
    path = _path(config)
    if path is not None:
        _clear(path)


def _current_config():
    return _CONFIG
