"""
拍内「舞台指令 vs 代码块」在**源码里**的顺序，与当前渲染出来的顺序对不对得上。

为什么要这一支：`render_beat_to_rpy` 里代码块的位置是**启发式**的
（有 `scene` 就插在第一条 scene 之后，否则插在所有 verb 之后），而它
**不在 `op_order` 里**（`op_order` 只记 stage/audio/voice）。
于是"过表之后代码跑到舞台前/后"这件事没有真源可比 —— 只能拿
`src_first..src_last` 这段**源码**当参照。

输出：
  · 每张"既有代码又有舞台"的拍：源码顺序 vs 渲染顺序，不一致的列出来；
  · `stage` 尾段（原生真迹）里、结构化列**不会**出的那些行 —— 它们的位置
    会随"尾段在哪调用"而移动，是这次要修的那一类。
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REAL = Path(r'D:\千连万理\RenPyTest\sr_general')
STAGE_VERBS = ('scene', 'show', 'hide')


def _src_lines(path: Path) -> list:
    return path.read_text(encoding='utf-8', errors='replace').splitlines()


def _src_order(lines: list, first: int, last: int) -> str:
    """源码区间内"舞台/代码/音频"的相对顺序（同类合并）。"""
    seq = []
    for ln in lines[first - 1:last]:
        t = ln.strip()
        if not t or t.startswith('#'):
            continue
        head = t.split(None, 1)[0]
        if head in STAGE_VERBS:
            c = 'S'
        elif head in ('play', 'stop', 'queue', 'voice'):
            c = 'A'
        elif head == 'python:' or t.startswith('$'):
            c = 'C'
        elif head in ('if', 'elif', 'else', 'menu', 'label', 'return', 'jump',
                      'call', 'pause', 'with', 'window', 'extend', 'init',
                      'screen', 'define', 'default', 'transform', 'image'):
            continue
        else:
            continue
        if not seq or seq[-1] != c:
            seq.append(c)
    return ''.join(seq)


def _render_order(lines: list) -> str:
    seq = []
    for ln in lines:
        t = ln.strip()
        if t.startswith(('#',)):
            continue
        if t.startswith(('scene ', 'show ', 'hide ')):
            c = 'S'
        elif t.startswith(('play ', 'stop ', 'queue ', 'voice ')):
            c = 'A'
        elif t.startswith(('$', 'python:')):
            c = 'C'
        else:
            continue
        if not seq or seq[-1] != c:
            seq.append(c)
    return ''.join(seq)


def main() -> int:
    from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
    from storygraph.story.beat_build import beats_from_parse_result
    from storygraph.story.beat_render import render_beat_to_rpy

    files = sorted(p for p in (REAL / 'game').rglob('*.rpy')
                   if not p.name.endswith('.generated.rpy'))
    stat = Counter()
    mismatch = []
    for p in files:
        pr, _ = IncrementalRenPyAnalyzer().analyze(files=[p], graph=None)
        beats = beats_from_parse_result(pr)
        lines = _src_lines(p)
        for i, b in enumerate(beats):
            has_code = bool(b.code or b.raw)
            has_stage = bool(b.scene or b.show or b.hide or b.stage)
            if not (has_code and has_stage):
                continue
            got = _render_order(render_beat_to_rpy(b))
            stat['既有代码又有舞台'] += 1
            stat[f'渲染顺序 {got}'] += 1
            if b.src_first and b.src_last and b.src_last >= b.src_first:
                want = _src_order(lines, b.src_first, b.src_last)
                stat[f'源码顺序 {want}'] += 1
                if want and got and want != got:
                    mismatch.append((p.name, i, b.src_first, b.src_last,
                                     want, got, b.stage, b.code or b.raw))
    print('统计：')
    for k, v in sorted(stat.items(), key=lambda kv: -kv[1]):
        print(f'  {v:>5}  {k}')
    print(f'\n源码顺序与渲染顺序**不一致**的拍：{len(mismatch)} 张')
    for name, i, a, z, want, got, stage, code in mismatch[:25]:
        print(f'  {name} 拍{i} 行{a}-{z}: 源码 {want} / 渲染 {got}')
        print(f'      stage={stage!r}')
        print(f'      code ={code!r}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
