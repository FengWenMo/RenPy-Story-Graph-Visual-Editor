"""列出两个项目里 raw_kind=unmodeled 的 show 原文。只读。"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from storygraph.parsing.discover import discover_scripts
from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
from storygraph.story.statement_registry import UNMODELED_KINDS

PROJECTS = [
    (r'D:\千连万理\RenPyTest\sr_general', '有 rules.json'),
    (r'D:\千连万理\sr_general', '无 rules.json'),
]


def main() -> int:
    for root, tag in PROJECTS:
        print('=' * 76)
        print(f'项目: {root}  [{tag}]')
        files = list(discover_scripts(Path(root)))[:400]
        pr, _graph = IncrementalRenPyAnalyzer().analyze(files=files, graph=None)
        rows = []
        for st in (getattr(pr, 'statements', None) or ()):
            p = st.payload if isinstance(getattr(st, 'payload', None), dict) else {}
            rk = p.get('raw_kind') or ''
            if getattr(st, 'kind', '') in ('loop', 'camera'):
                rk = getattr(st, 'kind')
            if rk not in UNMODELED_KINDS:
                continue
            text = str(p.get('text') or '')
            if not text.lstrip().startswith('show'):
                continue
            where = getattr(st, 'source', '') or ''
            if isinstance(where, (tuple, list)):
                src, line = (list(where) + ['', ''])[:2]
            else:
                src, line = where, ''
            rows.append((Path(str(src)).name, line, text.split('\n')[0].strip()))
        print(f'unmodeled show 共 {len(rows)} 条')
        shape = Counter()
        for name, line, text in rows:
            words = text.split()
            shape[f'{len(words)} 词'] += 1
        print('词数分布:', shape.most_common())
        for name, line, text in rows[:40]:
            print(f'   {name}:{line}  {text[:90]}')
        print()
    return 0


if __name__ == '__main__':
    sys.exit(main())
