#!/usr/bin/env python
"""
逐条定位剩余往返丢失（只读）
============================

`audit_roundtrip.py` 给的是**计数**；这个脚本把每一条"丢失/新增"配成
**最小对照**，并标出它属于哪一类原因：

  · `CONTINUATION` —— `python:` 块内**续行对齐空格**被规整（语义不变，
    只是排版被规整；审计把"同一段代码但缩进不同"算成一丢一增）
  · `MERGE`        —— 两条独立语句被并进同一张"代码"格（表示法差异）
  · `PARSER`       —— 解析器的行归属问题（不是往返丢的）
  · `QUOTE`        —— 引号/空格形态差异
  · `OTHER`

用法：
    python tools/diagnose_losses.py
    python tools/diagnose_losses.py --file script.rpy
"""
from __future__ import annotations

import argparse
import collections
import difflib
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer  # noqa
from storygraph.pipeline.compile import compile_beats  # noqa
from storygraph.story.beat_build import beats_from_parse_result  # noqa
from storygraph.story.excel_export import export_beats_to_excel  # noqa
from storygraph.story.excel_import import import_beats_from_excel  # noqa
from tools.audit_roundtrip import key, normalize_keys  # noqa

PROJECT = Path(r'D:\千连万理\RenPyTest\sr_general\game')


def _body(k) -> str:
    """
    从指纹里取"可读正文"。

    `_key` 的形状**按 kind 不同**（`('say', speaker, text)` /
    `('python', code)` / `('show', image, at, …)` …），所以不能统一取
    `k[1]` —— 那样 `say` 取到的是**说话人**，看着像空字符串（第一版就踩了）。
    """
    if not isinstance(k, (tuple, list)) or len(k) < 2:
        return str(k)
    kind = k[0]
    if kind in ('say', 'extend', 'comment', 'raw', 'code_line'):
        cands = [x for x in k[1:] if isinstance(x, str)]
        return max(cands, key=len) if cands else str(k)
    if kind == 'python':
        return str(k[1])
    cands = [str(x) for x in k[1:] if x not in ('', (), None)]
    return ' | '.join(cands) if cands else str(k)


def classify(kind: str, lost_text: str, added_texts: list) -> str:
    """
    给一条丢失猜一个原因标签。

    先按 **kind** 分：`code_line` ↔ `python` 这类"同一段文本被判成了不同
    种类"，是解析/分类问题（`PARSER`），不是排版漂移 —— 哪怕文本逐字
    相同也不该归到 `CONTINUATION`（第一版就是这么误报的）。
    """
    def _norm(t):
        return [ln.strip() for ln in str(t or '').split('\n') if ln.strip()]

    if kind == 'code_line':
        # `code_line` 的"对家"是 `python`（同一段文本、被判成了不同种类）
        return 'PARSER'

    a = _norm(lost_text)
    for t in added_texts:
        b = _norm(t)
        if a and a == b:
            return 'CONTINUATION'      # 逐行内容一样、只差缩进
    for t in added_texts:
        b = _norm(t)
        if a and b and a[:2] == b[:2]:
            return 'MERGE'             # 前几行一样，后面接上了别的东西
    if kind == 'python':
        return 'CONTINUATION'          # 多行代码块：只剩排版漂移这一类
    return 'OTHER'


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--project', default=str(PROJECT))
    ap.add_argument('--file', default='')
    args = ap.parse_args()

    project = Path(args.project)
    az = IncrementalRenPyAnalyzer()
    tmp = Path(tempfile.mkdtemp(prefix='diag_'))

    shown = 0
    by_reason = collections.Counter()
    for f in sorted(set(project.rglob('*.rpy'))):
        if args.file and args.file not in f.name:
            continue
        pr, _ = az.analyze(files=[f], graph=None)
        if not pr.statements:
            continue
        x = tmp / (f.stem + '.xlsx')
        export_beats_to_excel(beats_from_parse_result(pr), x)
        res = compile_beats(import_beats_from_excel(x))
        p2 = tmp / (f.stem + '_out.rpy')
        p2.write_text(res.rpy_text, encoding='utf-8')
        pr2, _ = az.analyze(files=[p2], graph=None)

        ca = normalize_keys(collections.Counter(key(s) for s in pr.statements))
        cb = normalize_keys(collections.Counter(key(s) for s in pr2.statements))
        lost, added = ca - cb, cb - ca
        if not lost:
            continue
        added_texts = [_body(k) for k in added]
        for k, v in lost.items():
            kind = k[0]
            body = _body(k)
            why = classify(kind, body, added_texts)
            by_reason[why] += v
            shown += 1
            print(f'=== [{why}] {f.name} x{v} · kind={kind} ===')
            print(f'  原文: {body[:150]!r}')
            # 找最像的对家
            best, best_r = None, 0.0
            for t in added_texts:
                r = difflib.SequenceMatcher(None, body, t).ratio()
                if r > best_r:
                    best, best_r = t, r
            if best is not None:
                print(f'  对家: {best[:150]!r}  (相似度 {best_r:.2f})')
                d = [ln for ln in difflib.unified_diff(
                    body.split('\n'), best.split('\n'),
                    'lost', 'added', lineterm='', n=0)][2:8]
                for ln in d:
                    print(f'        {ln}')
            print()

    print('=== 原因汇总 ===')
    for k, v in by_reason.most_common():
        print(f'  {k:<14} {v}')
    print(f'  合计 {sum(by_reason.values())}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
