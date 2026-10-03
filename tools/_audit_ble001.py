#!/usr/bin/env python
"""临时：把 201 处 `noqa: BLE001` 按"except 子句形状 + 体第一行"分类。"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HITS = []

for p in sorted((ROOT / 'storygraph').rglob('*.py')):
    lines = p.read_text(encoding='utf-8').splitlines()
    for i, ln in enumerate(lines):
        if 'noqa: BLE001' not in ln:
            continue
        j = i
        while j >= 0 and not lines[j].lstrip().startswith('except'):
            j -= 1
        exc = lines[j].strip() if j >= 0 else '?'
        indent = len(lines[j]) - len(lines[j].lstrip())
        body, k = [], j + 1
        while k < len(lines):
            s = lines[k]
            if s.strip() and (len(s) - len(s.lstrip())) <= indent:
                break
            if s.strip():
                body.append(s.strip())
            k += 1
        HITS.append((p.relative_to(ROOT), j + 1, exc,
                     body[0] if body else '(空体)', len(body)))

print('总数:', len(HITS))
print()
print('== except 子句形状 ==')
for k, v in Counter(h[2] for h in HITS).most_common(12):
    print('  %-42s %d' % (k, v))
print()
print('== except 体第一行形状 ==')


def shape(s: str) -> str:
    s = re.sub(r'"[^"]*"', '"S"', s)
    s = re.sub(r"'[^']*'", "'S'", s)
    return s[:58]


for k, v in Counter(shape(h[3]) for h in HITS).most_common(22):
    print('  %-60s %d' % (k, v))
print()
print('== 空体（只有 noqa 没有实际处理）==')
for p, ln, exc, first, nb in HITS:
    if first == '(空体)':
        print(f'  {p}:{ln}  {exc}')
