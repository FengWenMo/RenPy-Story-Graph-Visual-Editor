#!/usr/bin/env python
"""
探针：**拍内舞台指令的顺序**到底有没有被过表改掉（缺口 4 的正面对账）
====================================================================

为什么单独写一个系统性探针：`tools/probe_stage_order2.py` 只有 8 个手挑形状，
而这一类缺陷的形状空间是"同一拍里 2~4 条舞台指令的**排列**"。手挑容易漏，
漏掉的形状会在真机上以"玩家看到的收尾画面不同"的形式出现。

对账三方（**每一方都要和另外两方比**）：

    src   = 作者写的那几行（顺序的**真迹**）
    r1    = render(parse(src))                    ← 不过表
    r2    = render(parse(export→import(src)))     ← 过一趟 Excel

判定口径：

    · `r1 != r2` → **表把它改坏了**（这一条是缺口 4 的本体，必须为 0）；
    · `r1 != src` → 渲染器本身就没保住作者顺序（另一类问题，同样要看得见）。

只读，不写任何东西进项目。

    python tools/probe_stage_seq.py [--len 3] [--show 6]
"""

from __future__ import annotations

import argparse
import itertools
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer  # noqa: E402
from storygraph.story.beat_build import beats_from_parse_result  # noqa: E402
from storygraph.story.beat_render import render_beats_to_rpy  # noqa: E402
from storygraph.story.excel_export import export_beats_to_excel  # noqa: E402
from storygraph.story.excel_import import import_beats_from_excel  # noqa: E402

#: 舞台指令池：**不同 tag** 才会被排在同一个 verb 组里（同 tag 的 show/hide
#: 往往各自落拍，反而看不出分组问题）。
POOL = (
    'show ling happy',
    'show jc angry',
    'hide ling',
    'hide jc',
    'scene bg room',
    'with dissolve',
)


def _lines(text: str) -> list:
    return [ln.strip() for ln in text.splitlines()
            if ln.strip() and not ln.startswith('#')
            and ln.strip() not in ('label t:', 'return')]


def _render(src: str, tmp: Path, tag: str) -> tuple:
    d = tmp / tag
    d.mkdir(parents=True, exist_ok=True)
    f = d / 'ch.rpy'
    f.write_text('label t:\n' + ''.join(f'    {ln}\n' for ln in src)
                 + '    return\n', encoding='utf-8')
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)
    beats = beats_from_parse_result(pr)
    r1 = render_beats_to_rpy(beats)
    xl = d / 'ch.xlsx'
    export_beats_to_excel(beats, xl)
    r2 = render_beats_to_rpy(import_beats_from_excel(xl))
    return _lines(r1), _lines(r2)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--len', type=int, default=3, help='组合长度上限')
    ap.add_argument('--show', type=int, default=6, help='每种问题最多打印几个')
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix='probe_stage_seq_'))
    table_broke = []
    reordered = []          # 集合相同、顺序不同 → **真·翻序**
    dropped = []            # 少了指令 → 吞行
    total = 0
    for n in range(2, args.len + 1):
        for combo in itertools.product(POOL, repeat=n):
            total += 1
            src = list(combo)
            r1, r2 = _render(src, tmp, f'c{n}_{total}')
            if r1 != r2:
                table_broke.append((src, r1, r2))
            elif r1 != src:
                if sorted(r1) == sorted(src):
                    reordered.append((src, r1))
                else:
                    dropped.append((src, r1))
    print(f'组合 {total} 个（长度 2~{args.len}）')
    print()
    print(f'① 过表把顺序改坏（**必须为 0**）：{len(table_broke)} 个')
    for src, r1, r2 in table_broke[:args.show]:
        print(f'    源    : {src}')
        print(f'    不过表: {r1}')
        print(f'    过表后: {r2}')
    print()
    print(f'②-a 不过表就**翻序**（集合相同、顺序不同）：{len(reordered)} 个')
    for src, r1 in reordered[:args.show]:
        print(f'    源    : {src}')
        print(f'    产物  : {r1}')
    print()
    print(f'②-b 不过表就**吞行**（少指令）：{len(dropped)} 个')
    for src, r1 in dropped[:args.show]:
        print(f'    源    : {src}')
        print(f'    产物  : {r1}')
    return 1 if (table_broke or reordered or dropped) else 0


if __name__ == '__main__':
    raise SystemExit(main())
