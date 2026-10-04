"""
逐拍渲染快照 / 比对：改 `beat_render` 之前先钉住**每一张拍**渲染出的行。

为什么不用 `render_hash.py` 一条就够：那是**整份文件**的哈希，改了会红，
但**看不出改了哪张拍、顺序怎么变的**。改"拍内顺序"这类东西时必须逐拍比，
否则分不清"修好了那一处"与"顺手改坏了另外三十处"。

用法：
    python tools/snapshot_beat_render.py dump tools/.beat_render_baseline.json
    #   ...改代码...
    python tools/snapshot_beat_render.py diff tools/.beat_render_baseline.json

diff 只打印**变了的拍**（文件 · 拍号 · 前后行），并对每处变动的
"舞台/代码"相对顺序给一句可判断的说明。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REAL = Path(r'D:\千连万理\RenPyTest\sr_general')


def _dump() -> dict:
    from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
    from storygraph.story.beat_build import beats_from_parse_result
    from storygraph.story.beat_render import render_beat_to_rpy

    game = REAL / 'game'
    out = {}
    files = sorted(p for p in game.rglob('*.rpy')
                   if not p.name.endswith('.generated.rpy'))
    for p in files:
        pr, _ = IncrementalRenPyAnalyzer().analyze(files=[p], graph=None)
        beats = beats_from_parse_result(pr)
        out[p.name] = [render_beat_to_rpy(b) for b in beats]
    return out


def _classify(ln: str) -> str:
    t = ln.strip()
    if t.startswith(('scene ', 'show ', 'hide ')):
        return '舞台'
    if t.startswith(('#',)):
        return '注释'
    if t.startswith(('$', 'python:', 'if ', 'for ', 'while ', 'def ')):
        return '代码'
    if t.startswith(('play ', 'stop ', 'queue ', 'voice ')):
        return '音频'
    return '其它'


def _order(lines: list) -> str:
    """只留"舞台/代码/音频"的相对顺序，压成 `SCA` 这样的串（同类合并）。"""
    seq = []
    for ln in lines:
        c = _classify(ln)
        if c in ('舞台', '代码', '音频') and (not seq or seq[-1] != c):
            seq.append(c)
    return ''.join({'舞台': 'S', '代码': 'C', '音频': 'A'}[c] for c in seq)


def main(argv) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('mode', choices=('dump', 'diff'))
    ap.add_argument('path')
    args = ap.parse_args(argv[1:])

    cur = _dump()
    path = Path(args.path)
    if args.mode == 'dump':
        path.write_text(json.dumps(cur, ensure_ascii=False), encoding='utf-8')
        n = sum(len(v) for v in cur.values())
        print(f'已写 {path}（{len(cur)} 个文件 / {n} 张拍）')
        return 0

    base = json.loads(path.read_text(encoding='utf-8'))
    changed = 0
    order_changed = 0
    for name, old_beats in base.items():
        new_beats = cur.get(name)
        if new_beats is None:
            print(f'[文件消失] {name}')
            continue
        if len(old_beats) != len(new_beats):
            print(f'[拍数变了] {name}: {len(old_beats)} -> {len(new_beats)}')
        for i, (a, b) in enumerate(zip(old_beats, new_beats)):
            if a == b:
                continue
            changed += 1
            oa, ob = _order(a), _order(b)
            tag = '  ← 顺序变了' if oa != ob else ''
            if oa != ob:
                order_changed += 1
            print(f'[{name} 拍{i}]{tag}')
            print(f'    前: {oa}  {[ln.strip() for ln in a if _classify(ln) in ("舞台", "代码", "音频")]}')
            print(f'    后: {ob}  {[ln.strip() for ln in b if _classify(ln) in ("舞台", "代码", "音频")]}')
    print(f'\n变了的拍 {changed} 张，其中**顺序变了**的 {order_changed} 张')
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
