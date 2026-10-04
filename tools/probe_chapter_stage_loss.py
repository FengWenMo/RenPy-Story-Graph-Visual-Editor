"""
`chapter1_2.rpy` N3 差异的取证：`show cg cg02_2 at cg38402160 behind ye`
过表之后去哪了。

打法（每一步都把中间态打出来，别猜）：
  A. 解析 → 找出相关拍的**全部字段**（含 `raw` / `src_first..last`）；
  B. 直接渲染的行 + 它的行号；
  C. `to_row()` 那一行**逐格**打印（列名 = 值）；
  D. 只走 Excel（写盘再读回）后同一个拍的字段；
  E. 过表渲染的行 + 第一处差异 + 整份差异行数与后续是否只是"错位一行"。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REAL = Path(r'D:\千连万理\RenPyTest\sr_general')
NEEDLE = 'cg02_2'
FIELDS = ('kind', 'speaker', 'text', 'condition', 'scene', 'show', 'hide',
          'at', 'scene_at', 'with_', 'stage', 'stage_order', 'op_order',
          'raw', 'code', 'src_first', 'src_last', 'nest_path')


def _beats(path: Path):
    from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
    from storygraph.story.beat_build import beats_from_parse_result
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[path], graph=None)
    return beats_from_parse_result(pr)


def _dump(tag: str, i: int, b) -> None:
    print(f'  {tag}[{i}]')
    for f in FIELDS:
        v = getattr(b, f, None)
        if v not in ('', 0, None, []):
            print(f'      {f:<12} = {v!r}')
    if getattr(b, 'file_blocks', ''):
        print(f'      file_blocks  = <{len(b.file_blocks)} 字符>')


def _hit(b) -> bool:
    blob = ' '.join(str(getattr(b, f, '') or '') for f in
                    ('show', 'hide', 'stage', 'raw', 'text', 'code'))
    return NEEDLE in blob


def _lines(rpy: str) -> list:
    return [ln.rstrip() for ln in rpy.splitlines()
            if ln.strip() and not ln.startswith('#')]


def main() -> int:
    f = next(REAL.rglob('chapter1_2.rpy'), None)
    if f is None:
        print('（本机没有 chapter1_2.rpy）')
        return 0
    import tempfile
    from storygraph.story.beat_model import BEAT_FIELDS, HIDDEN_BEAT_COLUMNS
    from storygraph.story.beat_render import render_beats_to_rpy
    from storygraph.story.excel_export import export_beats_to_excel
    from storygraph.story.excel_import import import_beats_from_excel

    print('=' * 74)
    print(f'源文件：{f}')
    beats = _beats(f)
    print(f'拍数 {len(beats)}')
    hits = [i for i, b in enumerate(beats) if _hit(b)]
    print(f'含 {NEEDLE!r} 的拍：{hits}')
    for i in hits:
        _dump('R0', i, beats[i])

    r1 = _lines(render_beats_to_rpy(beats))
    for i, ln in enumerate(r1):
        if NEEDLE in ln or 'behind ye' in ln:
            print(f'  R1 行{i + 1}: {ln!r}')

    with tempfile.TemporaryDirectory(prefix='chstage_') as td:
        xl = Path(td) / 'c.xlsx'
        export_beats_to_excel(beats, xl)
        back = import_beats_from_excel(xl)
        print(f'  过表拍数 {len(back)}（原 {len(beats)}）')
        hits2 = [i for i, b in enumerate(back) if _hit(b)]
        print(f'  过表后含 {NEEDLE!r} 的拍：{hits2}')
        for i in hits2:
            _dump('R2', i, back[i])

        # 导出那一行逐格看
        i0 = hits[0]
        row = beats[i0].to_row()
        print(f'  R0[{i0}].to_row() 逐格：')
        for name, val in zip(BEAT_FIELDS, row):
            if str(val).strip():
                mark = '  ← 隐藏列' if name in HIDDEN_BEAT_COLUMNS else ''
                print(f'      {name:<14} = {str(val)!r}{mark}')

        r2 = _lines(render_beats_to_rpy(back))
        la, lb = r1, r2
        print(f'  R1 {len(la)} 行 / R2 {len(lb)} 行')
        diff = [i for i in range(max(len(la), len(lb)))
                if (la[i] if i < len(la) else None)
                != (lb[i] if i < len(lb) else None)]
        print(f'  逐行不同的位置 {len(diff)} 处：{diff[:12]}')
        for i in diff[:6]:
            x = la[i] if i < len(la) else '<缺行>'
            y = lb[i] if i < len(lb) else '<缺行>'
            print(f'    [{i + 1}] R1={x!r}\n         R2={y!r}')
        # R2 是不是 R1 去掉某一行后的结果（"只是少了一条"）
        for i in range(min(len(la), 40)):
            if [ln for ln in la[:i] + la[i + 1:]] == lb:
                print(f'  ⇒ R2 == R1 去掉第 {i + 1} 行：{la[i]!r}')
                break
        else:
            print('  ⇒ R2 不是"R1 少一行"的形状（还有其他差异）')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
