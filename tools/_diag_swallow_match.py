#!/usr/bin/env python
"""临时：对比 `ast` 判定的"静默 handler"与文本扫描找到的 except 行。"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / 'storygraph'
sys.path.insert(0, str(ROOT / 'tools'))
import _add_swallow as A          # noqa: E402

for p in sorted(PKG.rglob('*.py')):
    src = p.read_text(encoding='utf-8')
    lines = src.splitlines()
    silent = A._silent_body_lines(src)
    found = set()
    for i, raw in enumerate(lines):
        m = A.EXCEPT_RE.match(raw)
        if not m:
            continue
        j = i + 1
        while j < len(lines) and (not lines[j].strip()
                                  or lines[j].lstrip().startswith('#')):
            j += 1
        if (j + 1) in silent:
            found.add(j + 1)
    missed = sorted(silent - found)
    if not missed:
        continue
    print(f'== {p.relative_to(PKG)}  静默 {len(silent)}，找到 {len(found)}，'
          f'漏 {len(missed)}')
    for ln in missed[:4]:
        print(f'   体首行 {ln}: {lines[ln-1].strip()[:60]!r}')
        for k in range(max(0, ln - 4), min(len(lines), ln)):
            print(f'      {k+1:5d}| {lines[k]}')
