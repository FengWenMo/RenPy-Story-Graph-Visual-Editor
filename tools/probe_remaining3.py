"""
**三条收尾缺陷的回归钉**（2026-10-05 全部修完；这个文件从"诊断"变成"判据"）。

它原本是给下一轮定位用的取证脚本（打印"从哪来、为什么、该改哪一行"）。
三条都修好之后，打印没有意义了 —— 现在它**断言**，红了就是回退：

  ① 条件翻倍（N2 不动点）：`if flag0:` → `menu:` → 选项体里 `if flag2:`，
     产物再解析再渲染必须**逐字节**不变；
  ② `empty_option_body`：空选项体补的 `pass` 必须留在**它自己**那个选项下，
     台词不许被"吸"到空选项名下（从前两个选项体对调：选空的播台词）；
  ③ `chapter1_2.rpy` 的舞台指令顺序：`hide` / `show … behind ye` / `$ …`
     三行的相对顺序，直接渲染与过表渲染必须**逐字节**一致。

用法：
    python tools/probe_remaining3.py            # 断言，退出码 0/1
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


def _lines(rpy: str) -> list:
    return [ln.rstrip() for ln in rpy.splitlines()
            if ln.strip() and not ln.startswith('#')]


def _cli(text: str) -> list:
    """`label t:` 那一份产物里、**去掉 label/return 外壳**的行（带缩进）。"""
    out = []
    for ln in _lines(text):
        if ln.strip() in ('label t:', 'return'):
            continue
        out.append(ln[4:] if ln.startswith('    ') else ln)
    return out


def case_cond_double(tmp: Path) -> list:
    """① 条件不许翻倍（N2 不动点）。"""
    from storygraph.story.beat_render import render_beats_to_rpy
    print('=' * 72)
    print('① 条件翻倍（N2 不动点）')
    beats = _beats(DEPTH3, tmp)
    r1 = render_beats_to_rpy(beats)
    r2 = render_beats_to_rpy(_beats(r1, tmp, 'again1.rpy'))
    for ln in _lines(r1):
        print(f'    |{ln}')
    bad = []
    if _lines(r1) != _lines(r2):
        bad.append(f'R1 != R2：\n  {_lines(r1)}\n  {_lines(r2)}')
    conds = [ln.strip() for ln in _lines(r1) if ln.strip().startswith('if ')]
    if conds != ['if flag0:', 'if flag2:']:
        bad.append(f'里层条件多写了外层那一项：{conds}')
    return bad


def case_empty_body(tmp: Path) -> list:
    """② 空选项体不许把台词吸走。"""
    from storygraph.story.beat_render import render_beats_to_rpy
    from storygraph.story.excel_export import export_beats_to_excel
    from storygraph.story.excel_import import import_beats_from_excel
    print('=' * 72)
    print('② 空选项体：`pass` 归空的、台词归有内容的')
    beats = _beats(EMPTY, tmp)
    r1 = render_beats_to_rpy(beats)
    for ln in _lines(r1):
        print(f'    |{ln}')
    xl = tmp / 'e.xlsx'
    export_beats_to_excel(beats, xl)
    back = import_beats_from_excel(xl)
    r2 = render_beats_to_rpy(back)
    bad = []
    if _cli(r1) != _cli(r2):
        bad.append(f'两条路不一致：\n  {_cli(r1)}\n  {_cli(r2)}')
    want = ['menu:', '    "空的":', '        pass', '    "有内容":', '        e "x"']
    if _cli(r1) != want:
        bad.append(f'选项体归属错了：\n  实际 {_cli(r1)}\n  期望 {want}')
    return bad


def case_chapter(tmp: Path) -> list:
    """③ `chapter1_2.rpy` 的舞台/代码顺序（真实项目证据）。"""
    from storygraph.story.beat_render import render_beats_to_rpy
    from storygraph.story.excel_export import export_beats_to_excel
    from storygraph.story.excel_import import import_beats_from_excel
    print('=' * 72)
    print('③ chapter1_2.rpy 的舞台指令 vs 代码块顺序')
    f = next(REAL.rglob('chapter1_2.rpy'), None)
    if f is None:
        print('  （本机没有这个文件）—— 跳过')
        return []
    from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
    from storygraph.story.beat_build import beats_from_parse_result
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)
    beats = beats_from_parse_result(pr)
    r1 = _lines(render_beats_to_rpy(beats))
    xl = tmp / 'c.xlsx'
    export_beats_to_excel(beats, xl)
    r2 = _lines(render_beats_to_rpy(import_beats_from_excel(xl)))
    bad = []
    if r1 != r2:
        for i in range(max(len(r1), len(r2))):
            a = r1[i] if i < len(r1) else '<缺行>'
            b = r2[i] if i < len(r2) else '<缺行>'
            if a != b:
                bad.append(f'第 {i + 1} 行起不同：\n  直接: {a!r}\n  过表: {b!r}')
                break
    for a, b in zip(r1, r1[1:]):
        if 'behind ye' in a and b.strip().startswith('$'):
            print(f'    ok  {a.strip()!r}\n        在 {b.strip()[:40]!r} 之前')
            break
    else:
        bad.append('没找到 `show … behind ye` / `$ …` 那一对（用例形状变了？）')
    if not bad:
        print(f'    两条路逐字节一致（{len(r1)} 行）')
    return bad


def main() -> int:
    import tempfile
    bad: list = []
    with tempfile.TemporaryDirectory(prefix='diag_') as td:
        tmp = Path(td)
        bad += case_cond_double(tmp)
        bad += case_empty_body(tmp)
        bad += case_chapter(tmp)
    print('=' * 72)
    if bad:
        print(f'失败 {len(bad)} 处：')
        for b in bad:
            print(f'  ✗ {b}')
        return 1
    print('三条全部通过')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
