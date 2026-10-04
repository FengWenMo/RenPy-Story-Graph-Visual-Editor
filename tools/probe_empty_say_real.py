#!/usr/bin/env python
"""真实项目里有多少"kind=say 但台词为空"的拍？

（决定要不要为 `e ""` 改渲染：真实数据里一例都没有 → 记已知边界即可。）
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
from storygraph.story.beat_build import beats_from_parse_result

PROJECT = Path(r'D:\千连万理\RenPyTest\sr_general')


def main() -> int:
    if not PROJECT.exists():
        print(f'（真实项目不在：{PROJECT} → 跳过）')
        return 0
    files = sorted((PROJECT / 'game').rglob('*.rpy'))
    pr, _g = IncrementalRenPyAnalyzer().analyze(files=files, graph=None)
    beats = beats_from_parse_result(pr)
    empty_say = [b for b in beats
                 if b.kind == 'say' and not (b.text or '').strip()]
    empty_any = [b for b in beats if not (b.text or '').strip()]
    from collections import Counter
    print(f'beats 总数        {len(beats)}')
    print(f'台词为空（全部）  {len(empty_any)}')
    print(f'kind=say 且空台词 {len(empty_say)}')
    print('kind 分布         ', Counter(b.kind for b in beats).most_common())
    for b in empty_say[:8]:
        print('   ·', b.label, '| speaker=', repr(b.speaker),
              '| show=', repr(b.show)[:40], '| raw=', repr(b.raw)[:40])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
