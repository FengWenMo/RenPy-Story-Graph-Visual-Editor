#!/usr/bin/env python
"""
统计真实项目中"分支结构"在往返后的存活情况：
  elif / else / 分支内舞台指令 是否被拍扁。
"""
from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

PROJECT = Path(r'D:\千连万理\RenPyTest\sr_general\game')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer  # noqa
from storygraph.story.beat_build import beats_from_parse_result        # noqa
from storygraph.story.excel_export import export_beats_to_excel        # noqa
from storygraph.story.excel_import import import_beats_from_excel      # noqa
from storygraph.pipeline.compile import compile_beats                  # noqa

STAGE_KW = re.compile(r'^\s*(show|scene|hide)\s')


def main() -> int:
    files = sorted(set(PROJECT.rglob('*.rpy')))
    a = IncrementalRenPyAnalyzer()
    t = Path(tempfile.mkdtemp(prefix='br_'))

    src_elif = src_if = 0
    out_elif = out_if = out_not = 0
    # 分支内的舞台指令：原文里缩进在 if/elif 之下
    src_branch_stage = 0
    out_branch_stage = 0
    worst = []

    for f in files:
        try:
            src = f.read_text(encoding='utf-8').splitlines()
            pr, _ = a.analyze(files=[f], graph=None)
            beats = beats_from_parse_result(pr)
            if not beats:
                continue
            x = t / (f.stem + '.xlsx')
            export_beats_to_excel(beats, x)
            res = compile_beats(import_beats_from_excel(x))
            out = res.rpy_text.splitlines()
        except Exception as e:
            print('ERR', f.name, e)
            continue

        se = sum(1 for l in src if re.match(r'^\s*elif\b', l))
        si = sum(1 for l in src if re.match(r'^\s*if\b', l))
        oe = sum(1 for l in out if re.match(r'^\s*elif\b', l))
        oi = sum(1 for l in out if re.match(r'^\s*if\b', l))
        on = sum(1 for l in out if 'not ((' in l)
        src_elif += se
        src_if += si
        out_elif += oe
        out_if += oi
        out_not += on

        # 分支内舞台指令：上一非空行是 if/elif 且缩进更深
        def count_branch_stage(lines):
            n = 0
            prev = None
            for l in lines:
                if STAGE_KW.match(l) and prev is not None:
                    if re.match(r'^\s*(if|elif)\b', prev):
                        n += 1
                if l.strip():
                    prev = l
            return n

        sbs = count_branch_stage(src)
        obs = count_branch_stage(out)
        src_branch_stage += sbs
        out_branch_stage += obs
        if se and not oe:
            worst.append((f.relative_to(PROJECT.parent), se, sbs, obs))

    print('=== 分支结构存活情况（全项目 41 文件）===')
    print(f'  if    : 原文 {src_if:>4}  →  产物 {out_if:>4}')
    print(f'  elif  : 原文 {src_elif:>4}  →  产物 {out_elif:>4}'
          f'   （展开成 not((...)) 的行数：{out_not}）')
    print(f'  分支内舞台指令 : 原文 {src_branch_stage:>4}  →  '
          f'产物 {out_branch_stage:>4}')
    print()
    print('=== 有 elif 但产物里一条都没有的文件 ===')
    for name, se, sbs, obs in sorted(worst, key=lambda x: -x[1])[:12]:
        print(f'  elif {se:>2} 条，分支内舞台 {sbs}→{obs}   {name}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
