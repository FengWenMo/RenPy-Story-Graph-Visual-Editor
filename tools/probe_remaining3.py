"""
剩下三条的诊断（为下一轮铺路）：把"从哪来、为什么、该改哪一行"打出来。

  ① 条件翻倍（N2 不动点）：产物已是展平合取，再解析被**再累加**一次
  ② `empty_option_body`：空体补 `pass` 后结构位多一条 `menu:1>A`
  ③ `chapter1_2.rpy` 的 N3：源码那一行 vs 产物那一行（疑似真差异）
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REAL = Path(r'D:\千连万理\RenPyTest\sr_general')

DEPTH3 = (
    'label t:\n'
    '    if flag0:\n'
    '        menu:\n'
    '            "选项1":\n'
    '                if flag2:\n'
    '                    e "叶子"\n'
)
EMPTY = (
    'label t:\n'
    '    menu:\n'
    '        "空的":\n'
    '        "有内容":\n'
    '            e "x"\n'
)


def _beats(text: str, tmp: Path, name: str = 'ch.rpy'):
    from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
    from storygraph.story.beat_build import beats_from_parse_result
    f = tmp / name
    f.write_text(text, encoding='utf-8')
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)
    return beats_from_parse_result(pr)


def case_cond_double(tmp: Path) -> None:
    from storygraph.story.beat_render import render_beats_to_rpy
    print('=' * 72)
    print('① 条件翻倍')
    beats = _beats(DEPTH3, tmp)
    print('  解析出来的条件：')
    for b in beats:
        if (b.condition or '').strip():
            print(f'    {b.kind:<12} cond={b.condition!r:<34} '
                  f'nest={b.nest_path!r}')
    r1 = render_beats_to_rpy(beats)
    print('  R1：')
    for ln in r1.splitlines():
        if ln.strip() and not ln.startswith('#'):
            print(f'    |{ln}')
    r2 = render_beats_to_rpy(_beats(r1, tmp, 'again1.rpy'))
    print('  R2（把 R1 再解析一遍后渲染）：')
    for ln in r2.splitlines():
        if ln.strip() and not ln.startswith('#'):
            print(f'    |{ln}')
    print('  诊断：产物把**累积条件**写在里层 `if` 上，而它外面已经有外层 `if:`')
    print('        → 再解析时解析器又 `_combine` 一次外层条件 → 每轮更长。')
    print('  R1 里的条件行：')
    for ln in r1.splitlines():
        if ln.strip().startswith('if '):
            print(f'    {ln.strip()!r}')


def case_empty_body(tmp: Path) -> None:
    from storygraph.story.beat_render import render_beats_to_rpy
    from storygraph.story.excel_export import export_beats_to_excel
    from storygraph.story.excel_import import import_beats_from_excel
    print('=' * 72)
    print('② 空选项体：结构位多重集')
    beats = _beats(EMPTY, tmp)
    print('  原文路径：', [b.nest_path for b in beats])
    r1 = render_beats_to_rpy(beats)
    print('  R1：')
    for ln in r1.splitlines():
        if ln.strip() and not ln.startswith('#'):
            print(f'    |{ln}')
    again = _beats(r1, tmp, 'again2.rpy')
    print('  产物再解析的路径：', [b.nest_path for b in again])
    print('  诊断：空体补的 `pass` 在产物里是一张**普通拍**，再解析时它落进')
    print('        选项体的路径上 → 多重集多一条 `menu:1>A`。')
    xl = tmp / 'e.xlsx'
    export_beats_to_excel(beats, xl)
    back = import_beats_from_excel(xl)
    print('  过表后的路径：', [b.nest_path for b in back])


def case_chapter(tmp: Path) -> None:
    from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
    from storygraph.story.beat_build import beats_from_parse_result
    from storygraph.story.beat_render import render_beats_to_rpy
    from storygraph.story.excel_export import export_beats_to_excel
    from storygraph.story.excel_import import import_beats_from_excel
    print('=' * 72)
    print('③ chapter1_2.rpy 的 N3 差异')
    f = next(REAL.rglob('chapter1_2.rpy'), None)
    if f is None:
        print('  （本机没有这个文件）')
        return
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)
    beats = beats_from_parse_result(pr)
    r1 = render_beats_to_rpy(beats)
    xl = tmp / 'c.xlsx'
    export_beats_to_excel(beats, xl)
    r2 = render_beats_to_rpy(import_beats_from_excel(xl))

    def norm(t):
        return [ln.rstrip() for ln in t.splitlines()
                if ln.strip() and not ln.startswith('#')]
    a, b = norm(r1), norm(r2)
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            print(f'  第 {i + 1} 行起不同：')
            print(f'    直接渲染: {x!r}')
            print(f'    过表渲染: {y!r}')
            break
    # 找那条 show 的来源拍
    for i, bt in enumerate(beats):
        blob = f'{bt.show} {bt.stage} {bt.raw}'
        if 'cg02_2' in blob or 'cg38402160' in blob:
            print(f'  相关拍[{i}] kind={bt.kind} show={bt.show!r} '
                  f'stage={bt.stage!r} at={bt.at!r} scene_at={bt.scene_at!r} '
                  f'stage_order={bt.stage_order!r} op_order={bt.op_order!r}')
    print('  诊断线索：过表后那一拍少了/换了舞台指令 —— 看上面两个拍的字段差异。')


def main() -> int:
    import tempfile
    with tempfile.TemporaryDirectory(prefix='diag_') as td:
        tmp = Path(td)
        case_cond_double(tmp)
        case_empty_body(tmp)
        case_chapter(tmp)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
