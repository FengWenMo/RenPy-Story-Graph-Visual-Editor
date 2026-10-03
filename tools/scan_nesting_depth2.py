#!/usr/bin/env python
"""
在前一个扫描基础上**甄别**：深度 ≥3 的块，到底是
  (a) 演出流内的块嵌套（label 下的 if/if 或 if>menu>if）  ← 才需要表格支持
  (b) python 逻辑块 / screen / init 里的普通缩进          ← 本就不归表格管

做法是跟踪上下文：进入 `python:`、`screen`、`init`、`transform`、
`style` 等非演出区域时停止计入。
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

PROJECT = Path(r'D:\千连万理\RenPyTest\sr_general\game')

BLOCK_OPEN = re.compile(r'^\s*(if|elif|else|menu|while|for)\b.*:\s*$')
OPT_BODY = re.compile(r'^\s*"[^"]*"\s*:\s*$')
LABEL_RE = re.compile(r'^label\s+\w+\s*:')
NON_STAGE = re.compile(
    r'^\s*(screen|init|transform|style|default|define'
    r'|python:|init\s+python:|style\s+\w)')
# // 排除在 GC/p py 区域：python 块内的缩进 if
PYTHON_BLOCK = re.compile(r'^\s*(python:|init\s+python:|\$ )')


def scan(path: Path, stage_hist: Counter, py_hist: Counter, samples: dict):
    lines = path.read_text(encoding='utf-8', errors='replace').splitlines()
    stack = []
    in_python = False
    python_indent = -1
    seen_label = False

    for i, raw in enumerate(lines, 1):
        if not raw.strip() or raw.strip().startswith('#'):
            continue
        indent = len(raw) - len(raw.lstrip())
        s = raw.strip()

        if LABEL_RE.match(s):
            seen_label = True
            stack = []
            in_python = False
            continue

        # python 块进出
        if re.match(r'^\s*(init\s+)?python:\s*$', raw):
            in_python = True
            python_indent = indent
            continue
        if in_python:
            if indent > python_indent:
                # python 块内的控制语句单独计数，不算演出嵌套
                if BLOCK_OPEN.match(raw):
                    py_hist[len([x for x in stack if True]) + 1] += 1
                continue
            in_python = False

        # 非演出区域（screen / transform / style / init）
        if not seen_label:
            continue
        if re.match(r'^\s*(screen|transform|style)\s+\w', raw):
            stack = []
            continue

        while stack and indent <= stack[-1][0]:
            stack.pop()

        if BLOCK_OPEN.match(raw) or OPT_BODY.match(raw):
            kind = ('menu' if s.startswith('menu')
                    else 'opt' if OPT_BODY.match(raw) else 'if')
            stack.append((indent, kind))
            d = len(stack)
            stage_hist[d] += 1
            if d >= 3:
                samples.setdefault(d, []).append(
                    (path.name, i, ' > '.join(k for _, k in stack), s[:55]))


def main() -> int:
    stage = Counter()
    pyd = Counter()
    samples = {}
    deepest = []
    for f in sorted(set(PROJECT.rglob('*.rpy'))):
        try:
            before = max(stage) if stage else 0
            scan(f, stage, pyd, samples)
            after = max(stage) if stage else 0
            if after > before:
                deepest.append((f.relative_to(PROJECT.parent), after))
        except Exception as e:
            print('ERR', f.name, e)

    tot = sum(stage.values())
    print('=== 演出流内的块嵌套深度（排除 python/screen/transform/init）===')
    print(f'  演出块总数 {tot}')
    for d in sorted(stage):
        bar = '#' * min(40, stage[d] * 40 // max(stage.values()))
        print(f'  深度 {d}: {stage[d]:>5}  ({stage[d]*100.0/tot:5.1f}%)  {bar}')
    print()
    print(f'=== python 逻辑块内的控制结构：{sum(pyd.values())} 条'
          f'（不在演出流，本就不归表格管）===')
    print()
    for d in sorted(samples):
        print(f'=== 深度 {d} 的样例（{len(samples[d])} 条）===')
        for name, ln, chain, txt in samples[d][:8]:
            print(f'  {name}:{ln}  [{chain}]')
            print(f'      {txt}')
        print()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
