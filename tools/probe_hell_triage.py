#!/usr/bin/env python
"""
hell_test 抓到 54 处，这个脚本负责**定性**：
把每个失败用例的「原文 → 产物」逐行摆出来，人来判断
是真丢了，还是我的判据写错了。

用法：
    D:/Python/python.exe tools/probe_hell_triage.py 用例名...
    D:/Python/python.exe tools/probe_hell_triage.py adv_empty_text adv_only_speaker
    D:/Python/python.exe tools/probe_hell_triage.py --all      # 全部重跑并归类

只读。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))

import hell_test as H                # noqa: E402


def show(name: str, src: str) -> None:
    print('=' * 70)
    print(f'### {name}')
    print('-' * 70)
    print('【原文】')
    for i, ln in enumerate(src.splitlines(), 1):
        print(f'  {i:>3}| {ln}')
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        out, exploded, patched = H._roundtrip(src, tmp, name=f'{name}.rpy')
    print('【产物】' + ('' if not patched else '（增量已打补丁）'))
    if exploded:
        print('  ' + out)
        return
    for i, ln in enumerate(out.splitlines(), 1):
        print(f'  {i:>3}| {ln}')
    want = H._meaningful_lines(src)
    have = {H.squash(l) for l in out.splitlines()}
    lost = [l for l in want if H.squash(l) not in have]
    print('【丢了】')
    if not lost:
        print('  （无）')
    for l in lost:
        print(f'  ✗ {l!r}')


CASES = {}


def build_cases() -> None:
    for k, line in H.ADVERSARIAL.items():
        CASES[f'adv_{k}'] = f'label a:\n{line}\n'
    for k, src in H.STRUCTURES.items():
        CASES[f'struct_{k}'] = src
    body = 'label a:\n    e "台词"\n'
    for k, top in H.TOPLEVEL.items():
        for where in ('before', 'after', 'both'):
            CASES[f'top_{k}_{where}'] = H._combine(top, body, where)


def main(argv) -> int:
    build_cases()
    if not argv or argv[0] == '--all':
        names = list(CASES)
    else:
        names = argv
    for n in names:
        if n not in CASES:
            print(f'（没有这个用例：{n}）')
            continue
        show(n, CASES[n])
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
