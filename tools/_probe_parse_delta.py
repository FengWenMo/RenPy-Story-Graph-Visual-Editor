"""找出解析指纹变化的**具体语句**：新逻辑 vs 旧逻辑（把 `_say_tail` 打回空串）。"""
from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
from storygraph.parsing import renpy_parser_stmts as S

REAL = Path(r'D:\千连万理\renpyTest\sr_general')
if not REAL.is_dir():
    REAL = Path(r'D:\千连万理\RenpyTest\sr_general')

FILES = ['eyes_tracker.rpy', 'mini_game_dfinder.rpy', 'parallax.rpy',
         'script_pomo.rpy']


def trace(path):
    az = IncrementalRenPyAnalyzer()
    pr, _ = az.analyze(files=[path], graph=None)
    out = []
    for st in pr.statements:
        d = dataclasses.asdict(st) if dataclasses.is_dataclass(st) else dict(st)
        out.append(json.dumps(d, ensure_ascii=False, sort_keys=True))
    return out, pr


def main():
    for name in FILES:
        p = REAL / 'game' / name
        if not p.is_file():
            print(f'（没有 {name}）')
            continue
        orig = S._say_tail
        S._say_tail = lambda text, pos: ''          # 旧口径
        old, pr_old = trace(p)
        S._say_tail = orig                            # 新口径
        new, pr_new = trace(p)
        print(f'\n=== {name}: {len(old)} -> {len(new)} 条语句')
        for i, (a, b) in enumerate(zip(old, new)):
            if a != b:
                print(f'  [{i}] OLD {a[:200]}')
                print(f'      NEW {b[:200]}')
        if len(old) != len(new):
            print('  条数变了，看差值：')
            so, sn = set(old), set(new)
            for x in sorted(so - sn):
                print('   - ', x[:200])
            for x in sorted(sn - so):
                print('   + ', x[:200])


if __name__ == '__main__':
    main()
