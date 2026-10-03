#!/usr/bin/env python
"""
把 `except` 行的尾注释**只在参差时**对齐。

判据（比"取众数然后全改"稳）：**按缩进层级分组**，
组内注释列只要有分歧，就统一到**该组里出现最早**的那个列 ——
那通常是拆分前就写好的排版，也就是"原本的样子"。

如果组内本来就一致（绝大多数文件），**一个字都不动**。
第一版无条件套众数，把 `session_graph.py` 里本来好好的
`except Exception as e:            # noqa`（第 15 列）压成了第 3 列 ——
diff 噪声比参差本身还大。
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / 'storygraph'


def tidy(text: str) -> str:
    lines = text.splitlines(keepends=True)

    def parse(ln: str):
        if not ln.lstrip().startswith('except'):
            return None
        body = ln.rstrip('\n')
        i = body.find('#')
        if i < 0:
            return None
        code = body[:i]
        return code, body[i:], ('\n' if ln.endswith('\n') else '')

    groups = {}
    for ln in lines:
        got = parse(ln)
        if not got:
            continue
        code, _c, _nl = got
        key = len(code) - len(code.lstrip())
        groups.setdefault(key, []).append(len(code.rstrip()))

    target = {}
    for key, cols in groups.items():
        if len(set(cols)) <= 1:
            continue                      # 本来就齐 → 不动
        # 出现最早（= 最靠前的那一批）的列
        first = Counter(cols)
        _ = first
        target[key] = cols[0]

    if not target:
        return text

    out = []
    for ln in lines:
        got = parse(ln)
        if not got:
            out.append(ln)
            continue
        code, comment, nl = got
        code_r = code.rstrip()
        key = len(code) - len(code.lstrip())
        want = target.get(key)
        if want is None:
            out.append(ln)
            continue
        pad = ' ' * max(2, want - len(code_r))
        out.append(f'{code_r}{pad}{comment}{nl}')
    return ''.join(out)


def main(apply: bool) -> int:
    n = 0
    for p in sorted(PKG.rglob('*.py')):
        t = p.read_text(encoding='utf-8')
        new = tidy(t)
        if new == t:
            continue
        n += 1
        if apply:
            p.write_text(new, encoding='utf-8')
            print('  对齐', p.relative_to(PKG))
    print(f'\n{"已对齐" if apply else "待对齐"} {n} 个文件')
    return 0


if __name__ == '__main__':
    sys.exit(main(apply='--apply' in sys.argv))
