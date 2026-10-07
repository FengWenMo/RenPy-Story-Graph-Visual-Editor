#!/usr/bin/env python
"""
能力等级拆分探针：真机里每一级**到底是谁**？
==========================================

覆盖率报"100% T0"很好看，但唯一真源是**逐条语句**：
`core/statement.py` 的 kind + `payload['raw_kind']` + 语句头，
再交给 `statement_registry.capability_of` 定级。本脚本把三者摊开，
按 `(kind, raw_kind, 头)` 聚合计数 —— 用来回答"T0 里到底有多少是
**结构化列全功能编辑**、多少只是**原样透传但被算了进去**"。

只读，不写任何东西。

用法：
    python tools/probe_capability_breakdown.py [项目根] [--top 40]
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.core.vocabulary import statement_head_token  # noqa: E402
from storygraph.parsing.discover import discover_scripts  # noqa: E402
from storygraph.parsing.renpy_parser import (  # noqa: E402
    IncrementalRenPyAnalyzer)
from storygraph.story.statement_registry import (  # noqa: E402
    capability_of, registry_for_parse_result)

DEFAULT_REAL = r'D:\千连万理\RenPyTest\sr_general'


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('root', nargs='?', default=DEFAULT_REAL)
    ap.add_argument('--top', type=int, default=40)
    args = ap.parse_args()

    root = Path(args.root)
    scripts = discover_scripts(root)
    if not scripts:
        print(f'没扫到脚本：{root}')
        return 2
    az = IncrementalRenPyAnalyzer()
    pr, _ = az.analyze(files=scripts, graph=None)
    reg = registry_for_parse_result(pr)

    dims = Counter()          # (level, kind, raw_kind) → 条数
    heads = Counter()         # (level, head) → 条数
    for st in pr.statements:
        payload = st.payload if isinstance(st.payload, dict) else {}
        raw_kind = payload.get('raw_kind') or ''
        if st.kind == 'loop':
            raw_kind = 'loop'
        head = statement_head_token(payload.get('text') or '')
        cap = capability_of(head, reg, raw_kind=raw_kind)
        dims[(cap.level, st.kind, raw_kind or '-')] += 1
        heads[(cap.level, head or '（无头）')] += 1

    print(f'项目根：{root}')
    print(f'脚本 {len(scripts)} 个 · 语句 {len(pr.statements)} 条')
    print()
    print('—— 按 (等级, kind, raw_kind) ——')
    for (level, kind, rk), n in sorted(dims.items(),
                                       key=lambda kv: (-kv[1], kv[0])):
        print(f'  {level:4} {kind:12} raw_kind={rk:14} {n:5} 条')
    print()
    print(f'—— 等级里的语句头（前 {args.top}）——')
    for (level, head), n in sorted(heads.items(),
                                   key=lambda kv: (-kv[1], kv[0]))[:args.top]:
        print(f'  {level:4} {head:16} {n:5} 条')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
