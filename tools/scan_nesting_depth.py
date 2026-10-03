#!/usr/bin/env python
"""
扫描真实项目里"块嵌套"的实际深度分布。

这是回答"要不要支持任意层嵌套"的**唯一可靠依据**：
如果真实剧本根本没人写 3 层以上，那就不要为它付架构复杂度。

深度定义（只数演出流内的块结构）：
  label 体 = 0
  if/elif/else 体内 = +1
  menu 体内 = +1
  menu 选项体内 = +1
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

PROJECT = Path(r'D:\千连万理\RenPyTest\sr_general\game')

BLOCK_OPEN = re.compile(r'^\s*(if|elif|else|menu|while|for)\b.*:\s*$')
OPT_BODY = re.compile(r'^\s*"[^"]*"\s*:\s*$')


def scan_file(path: Path, hist: Counter, deep_examples: list):
    lines = path.read_text(encoding='utf-8', errors='replace').splitlines()
    stack = []          # (缩进, 类型)
    max_here = 0
    for i, raw in enumerate(lines, 1):
        if not raw.strip() or raw.strip().startswith('#'):
            continue
        indent = len(raw) - len(raw.lstrip())
        # 弹出所有缩进 >= 当前行的块
        while stack and indent <= stack[-1][0]:
            stack.pop()
        if BLOCK_OPEN.match(raw) or OPT_BODY.match(raw):
            kind = ('menu' if raw.strip().startswith('menu')
                    else 'opt' if OPT_BODY.match(raw)
                    else 'if')
            stack.append((indent, kind))
            depth = len(stack)
            hist[depth] += 1
            if depth > max_here:
                max_here = depth
            if depth >= 3 and len(deep_examples) < 12:
                deep_examples.append((path.name, i, depth, raw.strip()[:60]))
    return max_here


def main() -> int:
    hist = Counter()
    deep = []
    per_file_max = []
    for f in sorted(set(PROJECT.rglob('*.rpy'))):
        try:
            m = scan_file(f, hist, deep)
            if m:
                per_file_max.append((f.relative_to(PROJECT.parent), m))
        except Exception as e:
            print('ERR', f.name, e)

    total = sum(hist.values())
    print('=== 真实剧本 块嵌套深度分布 ===')
    print(f'  总块数 {total}')
    for d in sorted(hist):
        bar = '#' * min(40, hist[d] * 40 // max(hist.values()))
        print(f'  深度 {d}: {hist[d]:>5}  ({hist[d]*100.0/total:5.1f}%)  {bar}')
    print()
    print('=== 每个文件的最深嵌套 TOP10 ===')
    for name, m in sorted(per_file_max, key=lambda x: -x[1])[:10]:
        print(f'  深度 {m}  {name}')
    print()
    print(f'=== 深度 ≥3 的样例（{len(deep)} 条）===')
    for name, ln, d, txt in deep:
        print(f'  {name}:{ln}  深度{d}  {txt}')
    if not deep:
        print('  （无 —— 真实项目里没有一处 3 层嵌套）')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
