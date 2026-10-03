#!/usr/bin/env python
"""
定位 `layout_graph` 的不确定性出在哪一步。

用不同的 `PYTHONHASHSEED` 起子进程跑 `_trace_layout_child.py`，
比较它每阶段打印的摘要，最早出现分歧的那一步就是根因。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    rows = {}
    for seed in ('0', '1', '2', '3'):
        env = dict(os.environ, PYTHONHASHSEED=seed, SG_ROOT=str(ROOT))
        r = subprocess.run(
            [sys.executable, str(ROOT / 'tools' / '_trace_layout_child.py')],
            capture_output=True, text=True, cwd=str(ROOT), env=env,
            encoding='utf-8')
        out = {}
        for ln in (r.stdout or '').splitlines():
            parts = ln.split()
            if len(parts) == 2:
                out[parts[0]] = parts[1]
        if not out:
            print(f'seed {seed} 跑失败：{(r.stderr or "")[-400:]}')
            return 1
        rows[seed] = out

    seeds = list(rows)
    keys = list(rows[seeds[0]])
    print('%-18s %s' % ('阶段', '  '.join('seed' + s for s in seeds)))
    first_div = None
    for k in keys:
        vals = [rows[s].get(k, '?') for s in seeds]
        div = len(set(vals)) > 1
        if div and first_div is None:
            first_div = k
        print('%-18s %s%s' % (k, '  '.join(vals),
                              '   ← **最早分叉**' if div and k == first_div
                              else ('   ← 分叉' if div else '')))
    print()
    print('最早分叉的阶段：', first_div or '（没有分叉）')
    return 0 if first_div is None else 1


if __name__ == '__main__':
    sys.exit(main())
