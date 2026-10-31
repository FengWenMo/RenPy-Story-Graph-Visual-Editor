# -*- coding: utf-8 -*-
"""只读探针：**剧本注释 vs 编号表**对不对得上（同一份剧本配不同的表）。

用法：
    python tools/_probe_agreement.py [剧本项目根] [另一个项目的同名字符串目录…]

为什么要问这个：`# 表情A9` 这种注释是项目里**唯一**真正使用"第几号"的地方；
编号表要是和它对不上，换算出来的每一行都是错的 —— 而界面不会报错。

2026-11-01 的真事：`sr_general` 里只剩 `对应.txt`（重新编过号，把 `angry2`
从 8 号挪到 19 号），可剧本注释还按老编号写；老编号那份 `中英对应.txt` 只留在
`RenPyTest/sr_general` 里。这一跑把"谁和注释对得上"变成数字。
"""
from __future__ import annotations

import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.assets.sprite_codes import (          # noqa: E402
    _script_texts, agreement, parse_layeredimage,
    read_expression_tables, scan_script_text)

SCRIPT_ROOT = Path(r'D:\千连万理\sr_general')
TABLE_ROOTS = [
    Path(r'D:\千连万理\sr_general\game\images\characters'),
    Path(r'D:\千连万理\RenPyTest\sr_general\game\images\characters'),
]


def main(argv) -> int:
    script_root = Path(argv[1]) if len(argv) > 1 else SCRIPT_ROOT
    table_roots = [Path(a) for a in argv[2:]] or TABLE_ROOTS
    texts = _script_texts(script_root)
    known = {}
    for text in texts.values():
        for tag, groups in parse_layeredimage(text).items():
            slot = known.setdefault(tag, {'expr': [], 'outfit': []})
            for key in ('expr', 'outfit'):
                for name in groups[key]:
                    if name not in slot[key]:
                        slot[key].append(name)
    hits = []
    for rel, text in texts.items():
        hits.extend(scan_script_text(text, name=rel, known=known))
    votes = defaultdict(Counter)
    for hit in hits:
        if hit.expr:
            votes[hit.char][(hit.number, hit.expr)] += 1

    print('=' * 72)
    print(f'剧本：{script_root}（{len(texts)} 个文件 / {len(hits)} 条证据）')
    for tag in sorted(votes):
        total = sum(votes[tag].values())
        print(f'-- {tag}: 注释里 {total} 处带编号的证据 --')
        for root in table_roots:
            folder = root / tag
            if not folder.is_dir():
                continue
            files, conflicts = read_expression_tables(folder)
            if not files:
                continue
            for name, table in files.items():
                got = agreement(table, votes[tag])
                print(f'   {str(root.parent.parent.name):>10}/{name:<12} '
                      f'对上 {got:>3} / {total}（{got * 100 // max(total, 1)}%）'
                      f'  冲突 {len(conflicts)}')
    print('\n结论口径：对上最多的那份才和剧本说的是同一件事；'
          '对不上的那份即使文件更新，也只是"另一套编号"。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
