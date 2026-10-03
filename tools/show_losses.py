#!/usr/bin/env python
"""
列出真实项目往返中"真实丢失"的每一条 statement（归一化空白后），
连同所在文件、原文行号与产物对应位置，便于逐条定性。
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
    t = Path(tempfile.mkdtemp(prefix='loss_'))
    losses = []

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

            ca = collections.Counter(sig(s) for s in pr.statements)
            cb = collections.Counter(sig(s) for s in pr2.statements)
            lost = ca - cb
            if not lost:
                continue
            src_lines = f.read_text(encoding='utf-8').splitlines()
            out_lines = res.rpy_text.splitlines()
            for k, cnt in lost.items():
                # 在原文里定位
                where = ''
                for s in pr.statements:
                    if sig(s) == k:
                        ln = s.source[1] if s.source else 0
                        if 0 < ln <= len(src_lines):
                            where = src_lines[ln - 1].strip()
                        break
                # 产物里有没有"近似"的行（归一化后前缀相同）
                near = ''
                key_text = dict(k[1]).get('text') or dict(k[1]).get('code') or ''
                key_head = n(key_text)[:24]
                for ln in out_lines:
                    if key_head and key_head in n(ln):
                        near = ln.strip()
                        break
                losses.append((f.relative_to(PROJECT.parent), k, cnt,
                               where, near))
        except Exception as e:
            print('ERR', f.name, f'{type(e).__name__}: {e}')

    print(f'=== 真实丢失共 {sum(l[2] for l in losses)} 条 ===\n')
    for i, (fname, k, cnt, where, near) in enumerate(losses, 1):
        kind = k[0]
        payload = dict(k[1])
        body = (payload.get('text') or payload.get('code')
                or payload.get('image') or str(payload))
        print(f'--- 丢失 #{i}  [{kind}]  x{cnt}  {fname}')
        print(f'    原文行 : {where[:100]}')
        print(f'    内容   : {n(body)[:150]}')
        if near:
            print(f'    产物近 : {near[:100]}')
        else:
            print('    产物近 : （无相似行，完全消失）')
        print()
    return 0


if __name__ == '__main__':
    sys.exit(main())
