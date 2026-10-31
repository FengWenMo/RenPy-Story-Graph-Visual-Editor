"""真机归属审计：每条语句都必须有落点（P1 的验收脚本）。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.parsing.discover import discover_scripts
from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
from storygraph.story.beat_build import beats_from_parse_result

ROOTS = [
    r'D:\千连万理\RenPyTest\sr_general',
    r'D:\千连万理\sr_general',
]


def main() -> int:
    total_bad = 0
    for root in ROOTS:
        p = Path(root)
        if not p.is_dir():
            print(f'跳过（不存在）: {root}')
            continue
        files = list(discover_scripts(p))[:400]
        bad = 0
        stmts = 0
        for f in files:
            pr, _ = IncrementalRenPyAnalyzer().analyze(files=[str(f)], graph=None)
            stmts += len(pr.statements or ())
            report = []
            beats_from_parse_result(pr, report=report)
            if report:
                bad += len(report)
                print(f'  {Path(f).name}: {len(report)} 条未落点')
                for r in report[:3]:
                    print('     ', r)
        print(f'{p.name}: 文件 {len(files)} · 语句 {stmts} · 未落点 {bad}')
        total_bad += bad
    print(f'\n合计未落点: {total_bad}')
    return 1 if total_bad else 0


if __name__ == '__main__':
    raise SystemExit(main())
