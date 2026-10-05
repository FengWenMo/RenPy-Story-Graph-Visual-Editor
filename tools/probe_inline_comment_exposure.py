#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
probe_inline_comment_exposure.py
================================
**O3 的真实暴露面**：多行逻辑语句里那些行尾注释，到底在不在"注释通道"上？

清单（`docs/导出缺陷清单_2026-10-05.md`）里 O3 写的是"多行逻辑语句里的行尾
注释仍不采（设计如此，真实项目 0 处）"。这一轮复核发现**先前的数法不准**：
真实项目里有 38 处"续行上的行尾注释"，但它们**全部**落在

  · 文件级不透明块（`init python:` / `define` / `image` / `screen` /
    模块级赋值）—— 这些整块按**原文逐字**往返；
  · `python:` / `$` **代码块**（`CodeBlocks` 隐藏列）—— 同样逐字往返；

而 O3 说的"注释通道"（行尾注释被摘进 `Inline` 隐藏列）只作用于**单行逻辑
语句**。所以正确的口径是：

    续行行尾注释 38 处 → 全部逐字往返（不受 O3 影响）；
    落在**注释通道**上的多行逻辑语句行尾注释：**0 处**。

用法（只读真实项目，不改任何文件）：

    python tools/probe_inline_comment_exposure.py [项目根]
"""
from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

DEFAULT_ROOT = Path(r'D:\千连万理\RenPyTest\sr_general\game')

#: 字符串字面量（先摘掉，免得把 `"#efa3a6"` 这种当成注释）
_STR = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'')

#: 整块逐字往返的顶层块（撞上它们就不走"注释通道"）
OPAQUE_HEADS = ('init', 'define', 'image', 'screen', 'style', 'transform',
                'layeredimage', 'translate')


def scan(root: Path) -> tuple:
    """返回 (每个文件命中列表, 顶层块计数)。"""
    hits = []
    for f in sorted(root.rglob('*.rpy')):
        lines = f.read_text(encoding='utf-8', errors='replace').splitlines()
        depth = 0
        cont = False
        head = '<?'
        in_python_block = False
        for i, ln in enumerate(lines, 1):
            bare = _STR.sub('""', ln)
            code = bare.split('#', 1)[0]
            stripped = code.strip()
            if ln and not ln[0].isspace() and stripped:
                head = stripped.split()[0].rstrip(':')
                in_python_block = False
            if stripped in ('python:', 'init python:') or stripped.startswith(
                    ('python:', 'init python:')):
                in_python_block = True
            if (depth > 0 or cont) and '#' in bare and stripped:
                hits.append({
                    'file': f.name, 'line': i, 'head': head,
                    'in_code': in_python_block,
                    'opaque': head in OPAQUE_HEADS,
                    'text': bare.strip()[:70],
                })
            depth = max(depth + code.count('(') + code.count('[')
                        + code.count('{') - code.count(')')
                        - code.count(']') - code.count('}'), 0)
            cont = code.rstrip().endswith('\\')
    return hits, Counter(h['head'] for h in hits)


def main(argv: list) -> int:
    root = Path(argv[1]) if len(argv) > 1 else DEFAULT_ROOT
    if not root.exists():
        print(f'[跳过] 项目不存在：{root}')
        return 0
    files = sorted(root.rglob('*.rpy'))
    hits, by_head = scan(root)
    print(f'真实项目 .rpy：{len(files)} 个（{root}）')
    print(f'续行（多行逻辑语句内部）上的行尾注释：{len(hits)} 处')
    print('按顶层块归类：')
    for k, v in by_head.most_common():
        tag = '不透明块/代码块 → 逐字往返' if k in OPAQUE_HEADS else \
              ('label/其他' if k not in OPAQUE_HEADS else '')
        print(f'   {v:>3}  {k:<12} {tag}')
    in_code = [h for h in hits if h['in_code']]
    opaque = [h for h in hits if h['opaque']]
    channel = [h for h in hits
               if not h['in_code'] and not h['opaque']]
    print()
    print(f'  · 代码块（`python:` / `$` 多行）里：{len(in_code)} 处 → 逐字往返')
    print(f'  · 文件级不透明块（init/define/image/screen…）里：{len(opaque)} 处'
          f' → 逐字往返')
    print(f'  · 走**注释通道**的（O3 真正的影响面）：{len(channel)} 处')
    for h in channel:
        print(f'       {h["file"]}:{h["line"]}  {h["text"]}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
