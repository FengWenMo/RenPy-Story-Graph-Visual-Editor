#!/usr/bin/env python
"""
P1-1 验收：分支内舞台指令是否留在自己的分支里
=============================================

清单（`docs/问题清单与优化路线_2026-10-03.md`）说全项目"分支内舞台指令"
12 → 3，9 条被提到块外无条件执行。这个脚本量同一件事：

  · **原文**里每条舞台指令（scene/show/hide/play）各自带什么条件；
  · **产物**里它落在哪一层条件块里；
  · 再单独看 `script_pomo.rpy` 的 `pomoplace` 三分支。

用法：
    python tools/audit_branch_scope.py            # 全项目
    python tools/audit_branch_scope.py --file script_pomo
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer  # noqa
from storygraph.pipeline.compile import compile_beats  # noqa
from storygraph.story.beat_build import beats_from_parse_result  # noqa
from storygraph.story.excel_export import export_beats_to_excel  # noqa
from storygraph.story.excel_import import import_beats_from_excel  # noqa

STAGE = ('scene', 'show', 'hide', 'play')
#: 透传语句也算"有分支归属的语句"：`if flag:\n    pause 0.1` 里的
#: `pause 0.1` 以前会被吸进后面那张无条件拍，`if flag:` 整个消失。
#: 它同样是"静默改演出"，只是走的不是结构化列。
PASSTHROUGH = ('pause', 'window', 'with')


def branch_depth(lines: list) -> list:
    """逐行算"这一行处在几层 `if:` 块里"（只看缩进，够用）。"""
    stack = []
    out = []
    for ln in lines:
        s = ln.strip()
        if not s:
            out.append(len(stack))
            continue
        indent = len(ln) - len(ln.lstrip())
        while stack and indent <= stack[-1]:
            stack.pop()
        out.append(len(stack))
        if s.startswith(('if ', 'elif ', 'else:', 'menu:', 'while ')) \
                or s.endswith(':') and s.startswith(('if ', 'elif ')):
            stack.append(indent)
    return out


def _scoped(stmts) -> list:
    """带条件的语句（舞台指令 + 透传语句）。"""
    out = []
    for s in stmts:
        if not (s.condition or '').strip():
            continue
        if s.kind in STAGE:
            out.append(s)
            continue
        if s.kind == 'raw':
            text = ((s.payload or {}).get('text') or '').strip()
            head = text.split()[0] if text.split() else ''
            if head in PASSTHROUGH:
                out.append(s)
    return out



def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--project',
                    default=r'D:\千连万理\RenPyTest\sr_general\game')
    ap.add_argument('--file', default='')
    args = ap.parse_args()

    project = Path(args.project)
    az = IncrementalRenPyAnalyzer()
    tmp = Path(tempfile.mkdtemp(prefix='abs_'))

    total = scoped_ok = 0
    dropped = []
    for f in sorted(set(project.rglob('*.rpy'))):
        if args.file and args.file not in f.name:
            continue
        pr, _ = az.analyze(files=[f], graph=None)
        beats = beats_from_parse_result(pr)
        if not beats:
            continue
        x = tmp / (f.stem + '.xlsx')
        export_beats_to_excel(beats, x)
        res = compile_beats(import_beats_from_excel(x))
        p2 = tmp / (f.stem + '_out.rpy')
        p2.write_text(res.rpy_text, encoding='utf-8')
        pr2, _ = az.analyze(files=[p2], graph=None)

        want = _scoped(pr.statements)
        got = _scoped(pr2.statements)
        total += len(want)
        scoped_ok += min(len(want), len(got))
        if len(got) < len(want):
            dropped.append((f.name, len(want), len(got)))

    print('=== 带条件的语句（舞台指令 + pause/window/with 透传）===')
    print(f'  原文 {total} 条   →   产物里仍带条件 {scoped_ok} 条')
    if dropped:
        print('  丢分支保护的文件：')
        for name, a, b in dropped[:12]:
            print(f'    {name}: {a} → {b}')
    else:
        print('  **没有任何一条被提出分支**')

    if args.file:
        for f in sorted(set(project.rglob('*.rpy'))):
            if args.file not in f.name:
                continue
            pr, _ = az.analyze(files=[f], graph=None)
            beats = beats_from_parse_result(pr)
            x = tmp / (f.stem + '.xlsx')
            export_beats_to_excel(beats, x)
            res = compile_beats(import_beats_from_excel(x))
            p2 = tmp / (f.stem + '_out.rpy')
            p2.write_text(res.rpy_text, encoding='utf-8')
            lines = res.rpy_text.splitlines()
            depth = branch_depth(lines)
            print(f'\n=== 产物里带条件的舞台指令（{f.name}）===')
            for ln, d in zip(lines, depth):
                s = ln.strip()
                if s.startswith(STAGE) and d:
                    print(f'  [第 {d} 层] {s}')
            print(f'\n  `pomoplace` 在产物里出现 '
                  f'{res.rpy_text.count("pomoplace")} 次 '
                  f'（原文 {f.read_text(encoding="utf-8").count("pomoplace")} 次）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
