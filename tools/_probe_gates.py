"""逐条追问：127 条未建模语句，各自被哪道闸门拦下了。只读。"""
from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from storygraph.core.vocabulary import statement_head_token
from storygraph.parsing.discover import discover_scripts
from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
from storygraph.story.statement_registry import (UNMODELED_KINDS,
                                                 _character_names,
                                                 _is_builtin_head,
                                                 _looks_like_dialogue)

PROJECTS = [
    r'D:\千连万理\RenPyTest\sr_general',
    r'D:\千连万理\sr_general',
]


def main() -> int:
    for root in PROJECTS:
        print('=' * 72)
        print('项目:', root)
        files = list(discover_scripts(Path(root)))[:400]
        pr, _graph = IncrementalRenPyAnalyzer().analyze(files=files, graph=None)
        roles = _character_names(root)
        verdict = Counter()
        detail = []
        for st in (getattr(pr, 'statements', None) or ()):
            p = st.payload if isinstance(getattr(st, 'payload', None), dict) else {}
            rk = p.get('raw_kind') or ''
            if getattr(st, 'kind', '') in ('loop', 'camera'):
                rk = getattr(st, 'kind')
            if rk not in UNMODELED_KINDS:
                continue
            text = str(p.get('text') or '')
            head = statement_head_token(text)
            if not head:
                verdict['空头'] += 1
                continue
            if _is_builtin_head(head):
                verdict[f'内置头:{head}'] += 1
                continue
            if not re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', head):
                verdict[f'非标识符:{head}'] += 1
                continue
            sus = _looks_like_dialogue(head, text, roles)
            if sus:
                verdict[f'疑似台词:{head}'] += 1
                detail.append((head, '疑似台词', text.split('\n')[0][:70]))
                continue
            verdict[f'★该报未报:{head}'] += 1
            detail.append((head, '该报未报', text.split('\n')[0][:70]))
        print('闸门判定分布:')
        for k, v in verdict.most_common(20):
            print(f'   {k:<24} {v}')
        if detail:
            print('明细:')
            for head, why, line in detail[:20]:
                print(f'   [{why}] {head!r}: {line}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
