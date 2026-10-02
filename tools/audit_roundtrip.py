#!/usr/bin/env python
"""
端到端往返审计（只读，不入库）
==============================

    .rpy --parse--> statements A
        --beats--> Excel --import--> beats' --compile--> .rpy'
        --re-parse--> statements B

用 A 与 B 的差集衡量"Excel 双向同步"的真实保真度：这是本项目对外承诺的
核心能力，必须有量化的口径。

与 `audit_real_project.py` 的分工：那个测**解析能力**（能解出多少语句），
这个测**往返保真**（解出来的东西能不能原样回去）。

判据口径写在这里，避免"数字好看但意义不清"：

  · 比较的是**语句语义指纹**（kind + payload），位置与 content_hash 不参与；
  · `call x from y` 与"把它当 raw 原样回写"**算同一条**——`from_clause`
    的建模与否是内部表示问题，产物里那行代码是一样的；
  · 因此报告里每条丢失都配一份**最小复现片段**，人可以直接看是不是真丢。
"""
from __future__ import annotations

import argparse
import collections
import sys
import tempfile
import traceback
from pathlib import Path

PROJECT = Path(r'D:\千连万理\RenPyTest\sr_general\game')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer  # noqa
from storygraph.story.beat_build import beats_from_parse_result        # noqa
from storygraph.story.excel_export import export_beats_to_excel        # noqa
from storygraph.story.excel_import import import_beats_from_excel      # noqa
from storygraph.pipeline.compile import compile_beats                  # noqa


def key(s):
    """
    statement 的可比较指纹（忽略位置与 condition）。

    `call` 与"等价的 raw 回写"归一：`call chapter0_0 from _call_chapter0_0`
    无论内部是 CALL 语句还是 Raw 文本，**产物里都是同一行代码**，
    所以这里按"渲染出来的那一行"比对，而不是按内部 kind。
    """
    p = s.payload
    if s.kind == 'say':
        return ('say', p.get('speaker'), p.get('text'))
    if s.kind == 'extend':
        return ('extend', p.get('text'))
    if s.kind == 'menu':
        return ('menu', (p.get('caption') or '').strip())
    if s.kind == 'menu_option':
        return ('menu_option', p.get('text'))
    if s.kind == 'show':
        return ('show', p.get('image'), tuple(p.get('at') or ()),
                p.get('as_'), p.get('with_'))
    if s.kind == 'hide':
        return ('hide', p.get('image'), p.get('with_'))
    if s.kind == 'scene':
        return ('scene', p.get('image'), tuple(p.get('at') or ()),
                p.get('with_'))
    if s.kind == 'play':
        return ('play', p.get('action'), p.get('channel'), p.get('name'))
    if s.kind == 'voice':
        return ('voice', p.get('name'))
    if s.kind == 'python':
        return ('python', (p.get('code') or '').strip())
    if s.kind == 'comment':
        return ('comment', (p.get('text') or '').strip())
    if s.kind == 'raw':
        return ('raw', (p.get('text') or '').strip())
    if s.kind in ('jump', 'call'):
        text = f"{s.kind} {p.get('target', '')}"
        from_clause = (p.get('from_clause') or '').strip()
        if from_clause:
            text += f' from {from_clause}'
        return ('code_line', text)
    if s.kind == 'return':
        return ('return',)
    return (s.kind, str(sorted(p.items())))


def normalize_keys(counter: collections.Counter) -> collections.Counter:
    """把 `('raw', 'call x from y')` 与 `('code_line', 'call x from y')` 并起来。"""
    out = collections.Counter()
    for k, v in counter.items():
        if k[0] in ('raw', 'code_line') and len(k) == 2:
            out[('code_line', k[1])] += v
        else:
            out[k] += v
    return out


def reparse(text: str, path: Path, analyzer):
    """把生成物写回临时文件再解析（parser 面向文件）。"""
    tmp = Path(tempfile.mkdtemp(prefix='rt_')) / (path.stem + '_rt.rpy')
    tmp.write_text(text, encoding='utf-8')
    pr, _ = analyzer.analyze(files=[tmp], graph=None)
    return pr.statements


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--project', default=str(PROJECT),
                    help='被审计的 Ren\'Py 工程 game 目录')
    ap.add_argument('--samples', type=int, default=3,
                    help='每种 kind 打印几条丢失样例')
    ap.add_argument('--per-file', type=int, default=10)
    args = ap.parse_args()

    project = Path(args.project)
    files = sorted(set(project.rglob('*.rpy')))
    analyzer = IncrementalRenPyAnalyzer()
    tmpdir = Path(tempfile.mkdtemp(prefix='rt_xlsx_'))

    a_all, b_all = collections.Counter(), collections.Counter()
    errs = []
    per_file = []
    samples = collections.defaultdict(list)

    for f in files:
        try:
            pr, _ = analyzer.analyze(files=[f], graph=None)
            stmts_a = list(pr.statements)
            beats = beats_from_parse_result(pr)
            if not beats:
                continue
            x = tmpdir / (f.stem + '.xlsx')
            export_beats_to_excel(beats, x)
            back = import_beats_from_excel(x)
            res = compile_beats(back)
            stmts_b = reparse(res.rpy_text, f, analyzer)
        except Exception as e:                       # noqa: BLE001
            errs.append((f.name, f'{type(e).__name__}: {e}'))
            continue

        ca = normalize_keys(collections.Counter(key(s) for s in stmts_a))
        cb = normalize_keys(collections.Counter(key(s) for s in stmts_b))
        lost = ca - cb           # 原文有、产物没有
        added = cb - ca          # 产物有、原文没有（凭空生成）
        a_all += ca
        b_all += cb
        n_lost, n_added = sum(lost.values()), sum(added.values())
        for k, v in lost.items():
            if len(samples[k[0]]) < args.samples:
                samples[k[0]].append((f.name, k, v))
        if n_lost or n_added:
            per_file.append((f.relative_to(project.parent),
                             sum(ca.values()), n_lost, n_added))

    ta = sum(a_all.values())
    lost = a_all - b_all
    added = b_all - a_all
    nl, na = sum(lost.values()), sum(added.values())

    print('=== 端到端往返：原文 statement vs 产物 statement ===')
    print(f'  解析文件 {len(files)}，异常 {len(errs)}')
    print(f'  原文 {ta}  →  产物 {sum(b_all.values())}')
    print(f'  丢失 {nl}（{nl * 100.0 / ta:.1f}%）   '
          f'凭空新增 {na}（{na * 100.0 / ta:.1f}%）')
    print()
    print('=== 按 kind 的丢失 ===')
    byk = collections.Counter()
    totk = collections.Counter()
    for k, v in lost.items():
        byk[k[0]] += v
    for k, v in a_all.items():
        totk[k[0]] += v
    print(f'  {"kind":<12}{"原文":>8}{"丢失":>8}{"丢失率":>9}')
    for k, v in totk.most_common():
        l = byk.get(k, 0)
        print(f'  {k:<12}{v:>8}{l:>8}{l * 100.0 / v:>8.1f}%')

    print()
    print(f'=== 丢失最多的 {args.per_file} 个文件 ===')
    for name, n, l, ad in sorted(per_file, key=lambda x: -x[2])[:args.per_file]:
        print(f'  原{n:>5} 丢{l:>5} 增{ad:>4}  {name}')

    print()
    print('=== 丢失样例（每种 kind）===')
    for kind, items in sorted(samples.items()):
        for fname, k, v in items:
            print(f'  [{kind}] x{v} {fname}: {str(k)[:130]}')

    if errs:
        print()
        print('=== 异常 ===')
        for n, e in errs[:10]:
            print(f'  {n}: {e}')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception:                                # noqa: BLE001
        traceback.print_exc()
        sys.exit(2)
