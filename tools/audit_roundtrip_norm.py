#!/usr/bin/env python
"""
往返审计（归一化空白版）：排除纯空格差异，得到"语义级"真实丢失。
同时统计降级率与新增内容构成。
"""
from __future__ import annotations

import collections
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


def n(x):
    return re.sub(r'\s+', ' ', str(x or '')).strip()


def sig(s):
    return (s.kind, tuple(sorted((k, n(v)) for k, v in s.payload.items())))


def main() -> int:
    files = sorted(set(PROJECT.rglob('*.rpy')))
    a = IncrementalRenPyAnalyzer()
    t = Path(tempfile.mkdtemp(prefix='rtn_'))
    ca, cb = collections.Counter(), collections.Counter()

    for f in files:
        try:
            pr, _ = a.analyze(files=[f], graph=None)
            beats = beats_from_parse_result(pr)
            if not beats:
                continue
            x = t / (f.stem + '.xlsx')
            export_beats_to_excel(beats, x)
            res = compile_beats(import_beats_from_excel(x))
            out = t / (f.stem + '_o.rpy')
            out.write_text(res.rpy_text, encoding='utf-8')
            pr2, _ = a.analyze(files=[out], graph=None)
            ca += collections.Counter(sig(s) for s in pr.statements)
            cb += collections.Counter(sig(s) for s in pr2.statements)
        except Exception as e:
            print('ERR', f.name, f'{type(e).__name__}: {e}')

    ta = sum(ca.values())
    lost, added = ca - cb, cb - ca
    nl, na = sum(lost.values()), sum(added.values())
    print(f'原文 statement {ta}')
    print(f'真实丢失 {nl} ({nl*100.0/ta:.2f}%)   新增 {na} '
          f'({na*100.0/ta:.2f}%)')

    deg = sum(v for k, v in ca.items()
              if k[0] in ('raw', 'unknown', 'unmodeled'))
    print(f'降级率（raw/unknown）{deg} ({deg*100.0/ta:.1f}%)')

    print('\n=== 按 kind 丢失（仅列出有丢失的）===')
    totk, byk = collections.Counter(), collections.Counter()
    for k, v in ca.items():
        totk[k[0]] += v
    for k, v in lost.items():
        byk[k[0]] += v
    for k, v in totk.most_common():
        if byk.get(k):
            print(f'  {k:<12}{v:>6} 丢 {byk[k]:>3} '
                  f'({byk[k]*100.0/v:.1f}%)')

    print('\n=== 新增内容构成（TOP8）===')
    addk = collections.Counter()
    for k, v in added.items():
        addk[k[0]] += v
    for k, v in addk.most_common(8):
        print(f'  {k:<12}+{v}')
    print('\n  新增样例：')
    for i, k in enumerate(list(added)[:5]):
        print(f'   [{k[0]}] {str(k[1])[:90]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
