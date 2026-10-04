#!/usr/bin/env python
"""
真实项目：**顶层语句**（`default` / `define` / `transform` / `screen` /
`image` / `init python`）在**全量**路径上保住多少？

为什么这一档决定"能不能交付"：
交付评审的三条边界里有一条是"**全量（不给 --source）会丢第三类，
靠 OPAQUE_LOST 拒写**"。2026-10-05 晚，`parsing` 新增
`TopLevelRegionNode` —— **整个顶层区间**按原文进 `file_blocks`
（不再只有 `init python:`）。那条边界可能已经不成立了。

边界不成立 = 全量路径也能改已有文件 = 交付能力**质变**。
所以必须用真实项目量一遍，而不是看 demo。

只读。
"""
from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
from storygraph.story.beat_build import beats_from_parse_result
from storygraph.story.beat_render import render_beats_to_rpy

PROJECT = Path(r'D:\千连万理\RenPyTest\sr_general')

#: 顶层语句的块头（顶格、以这些词开头）
HEADS = ('default ', 'define ', 'transform ', 'screen ', 'image ',
         'init python:', 'init ', 'style ', '$')


def count(text: str) -> dict:
    """按**整行内容**去重计数（抹空白，与 `tools/hell_test.py` 同口径）。

    ⚠ 判据教训（2026-10-05，同一处踩了两次）：

      · 第一版按**行**数（不去重）→ `transform` 报 -9。
        那是假警报：源里同名 transform 定义多次，渲染层有意"内容相同
        只写一次"（重复定义会让后者覆盖，是 Ren'Py 的语义）。
      · 第二版按**块名**去重 → 更错：`define config.xxx` 全被并成
        `config`，凭空造出 -5。

    正确的一档是**按整行内容去重**：同名不同内容的都留着（那是覆盖语义），
    完全重复的行只算一次（那是有意的去重）。差集才是真丢。
    """
    out: dict = {}
    for ln in text.splitlines():
        if not ln.strip() or ln[:1].isspace():
            continue
        for h in HEADS:
            if ln.startswith(h):
                out.setdefault(h.strip(), set()).add(''.join(ln.split()))
                break
    return {k: len(v) for k, v in out.items()}


def blocks_of(text: str) -> list:
    """源文件里的**顶层块**：一个块头 + 它下面所有缩进行（含空行）。

    判据为什么必须是"块"而不是"行"（同一处踩了三次，写下来免得再改回去）：

      · 按行不去重 → 把"同名 transform 只写一次"当成丢失（假警报）；
      · 按块名去重 → `define config.xxx` 全并成 `config`（更假）；
      · 按整行去重 → `init python:` 块头都一样，21 个块并成 3 个（还是假）。

    块才是"作者写的东西"的单位：块头 + 它的体。
    """
    lines = text.splitlines()
    out, i = [], 0
    while i < len(lines):
        ln = lines[i]
        if not ln.strip() or ln[:1].isspace():
            i += 1
            continue
        if not any(ln.startswith(h) for h in HEADS):
            i += 1
            continue
        head, body = ln, [ln]
        i += 1
        while i < len(lines):
            nxt = lines[i]
            if not nxt.strip():          # 空行：块的延续（后面还有缩进就继续）
                body.append('')
                i += 1
                continue
            if nxt[:1].isspace():
                body.append(nxt)
                i += 1
                continue
            break
        while body and not body[-1].strip():
            body.pop()
        out.append((head, '\n'.join(body)))
    return out


def main() -> int:
    if not PROJECT.exists():
        print(f'（真实项目不在：{PROJECT} → 跳过）')
        return 0
    files = sorted((PROJECT / 'game').rglob('*.rpy'))
    all_blocks = []
    for f in files:
        for head, body in blocks_of(
                f.read_text(encoding='utf-8', errors='replace')):
            all_blocks.append((f.name, head, body))

    pr, _g = IncrementalRenPyAnalyzer().analyze(files=files, graph=None)
    beats = beats_from_parse_result(pr)
    out = render_beats_to_rpy(beats)            # ← 全量路径，不给源
    flat = ''.join(out.split())

    print(f'真实项目 {PROJECT}（全量路径 · 不给 --source）')
    print(f'  文件 {len(files)}   拍 {len(beats)}')
    print(f'  顶层块 {len(all_blocks)}')

    lost = [(f, h) for f, h, b in all_blocks
            if ''.join(b.split()) not in flat]
    print(f'  **整块不在产物里** {len(lost)}')
    for f, h in lost[:15]:
        print(f'    ✗ {f}: {h[:60]}')
    if not lost:
        print('  → 每一个顶层块都原样回到了产物里')
    return 1 if lost else 0


if __name__ == '__main__':
    raise SystemExit(main())
