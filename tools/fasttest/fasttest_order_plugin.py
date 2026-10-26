# -*- coding: utf-8 -*-
"""
fasttest 的 pytest 插件：**只做一件事** —— 按历史耗时把重文件排到收集队列
最前面，让 xdist 一开始就把真机金标摊到多个进程里，而不是最后一起等。

为什么不改测试、不加 marker：
  · 我们不需要"跳过/筛选"，只需要"先发谁"；
  · 权重放在 seed.json（提交）+ .pip-tmp/fasttest/durations.json（本机学习），
    由 tools/fasttest/run.py 维护，插件只读；
  · 收集顺序变了，但**文件内顺序不变**；需要原始顺序时传
    `--fasttest-no-order`（run.py 的 `--no-order` 会传）。

插件由 run.py 通过 `-p fasttest_order_plugin` 加载（PYTHONPATH 指向本目录）。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

SEED_PATH = Path(__file__).with_name('seed.json')

#: 这一条会**递归扫 `.pip-tmp`** 来数临时产物。它必须排在最前面：
#: 一旦别的测试先跑，current basetemp 里已经堆了几千个目录，这条会从
#: 秒级变成几分钟（2026-10-25 实测：391s）。它只读，先跑不影响任何判据。
PRIORITY_FILES = {'tests/test_disk_hygiene.py'}
PRIORITY_WEIGHT = 1_000_000_000.0


def pytest_addoption(parser):
    group = parser.getgroup('fasttest')
    group.addoption(
        '--fasttest-no-order', action='store_true', default=False,
        help='保持 pytest 默认收集顺序（不按历史耗时把重文件提前）')


def _load_files(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    files = data.get('files')
    return files if isinstance(files, dict) else {}


def _weights() -> dict:
    merged = _load_files(SEED_PATH)
    runtime = os.environ.get('FASTTEST_WEIGHTS')
    if runtime:
        merged.update(_load_files(Path(runtime)))
    return merged


def _relative(item, root: Path) -> str:
    raw = getattr(item, 'path', None)
    if raw is None:
        raw = getattr(item, 'fspath', None)
    try:
        return Path(str(raw)).resolve().relative_to(root).as_posix()
    except (ValueError, OSError):
        return Path(str(raw)).as_posix()


def pytest_collection_modifyitems(session, config, items):
    if config.getoption('fasttest_no_order', default=False):
        return
    if len(items) < 2:
        return
    root = Path(str(config.rootpath)).resolve()
    weights = _weights()

    groups: dict[str, list] = {}
    order: list[str] = []
    for item in items:
        rel = _relative(item, root)
        bucket = groups.get(rel)
        if bucket is None:
            bucket = groups[rel] = []
            order.append(rel)
        bucket.append(item)
    if len(order) < 2:
        return

    def estimate(rel: str) -> float:
        if rel in PRIORITY_FILES:
            return PRIORITY_WEIGHT
        value = weights.get(rel)
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
        try:
            size = (root / rel).stat().st_size
        except OSError:
            size = 20_000
        # 没测过的文件：按体积粗估，至少 0.5s；只影响先后，不影响判据
        return max(0.5, size / 25_000.0)

    # Python 的 sort 稳定：权重相同 → 保持原来文件间顺序
    order.sort(key=lambda rel: -estimate(rel))
    items[:] = [item for rel in order for item in groups[rel]]
