"""细查 discover_undeclared 到底扫到了什么：文件数、语句数、raw_kind 分布。只读。"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from storygraph.parsing.discover import discover_scripts
from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
from storygraph.story.statement_registry import UNMODELED_KINDS

PROJECTS = [
    r'D:\千连万理\RenPyTest\sr_general',
    r'D:\千连万理\sr_general',
]


def main() -> int:
    for root in PROJECTS:
        print('=' * 72)
        print('项目:', root)
        files = list(discover_scripts(Path(root)))[:400]
        print('discover_scripts 文件数:', len(files))
        pr, _graph = IncrementalRenPyAnalyzer().analyze(files=files, graph=None)
        sts = list(getattr(pr, 'statements', None) or ())
        print('解析出的语句数:', len(sts))
        kinds = Counter(getattr(s, 'kind', '') for s in sts)
        print('kind 分布(前 12):', kinds.most_common(12))
        unm = Counter()
        for s in sts:
            p = s.payload if isinstance(getattr(s, 'payload', None), dict) else {}
            rk = p.get('raw_kind') or ''
            if getattr(s, 'kind', '') in ('loop', 'camera'):
                rk = getattr(s, 'kind')
            if rk in UNMODELED_KINDS:
                unm[rk] += 1
        print('UNMODELED_KINDS 命中:', sum(unm.values()), unm.most_common(8))
        show_n = sum(1 for s in sts
                     if str((s.payload or {}).get('text') or '').lstrip().startswith('show '))
        print('以 show 开头的语句行:', show_n)
    return 0


if __name__ == '__main__':
    sys.exit(main())
