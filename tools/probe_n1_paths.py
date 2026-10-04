"""
N1（结构位多重集）真实项目那几处的取证：把**两边**的 `label|nest_path`
逐条列出来（带上"这一拍渲染成什么"），而不是只看聚合出来的"少/多"。

用法：
    python tools/probe_n1_paths.py [文件名…]      # 默认：hell 报的那 5 个
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REAL = Path(r'D:\千连万理\RenPyTest\sr_general')
DEFAULT = ['credits_screen.rpy', 'mini_game1.rpy', 'mini_game_dfinder.rpy',
           'mini_game_puzzle.rpy', 'pomodorro.rpy']


def _paths(beats: list) -> Counter:
    return Counter(f'{getattr(b, "label", "")}|{getattr(b, "nest_path", None)}'
                   for b in beats)


def _what(beat) -> str:
    from storygraph.story.beat_render import render_beat_to_rpy
    lines = [ln.strip() for ln in render_beat_to_rpy(beat) if ln.strip()]
    return ' / '.join(lines[:2])


def main(argv) -> int:
    import tempfile
    from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
    from storygraph.story.beat_build import beats_from_parse_result
    from storygraph.story.beat_render import render_beats_to_rpy
    from storygraph.story.excel_export import export_beats_to_excel
    from storygraph.story.excel_import import import_beats_from_excel

    want = argv[1:] or DEFAULT
    files = {p.name: p for p in (REAL / 'game').rglob('*.rpy')}
    with tempfile.TemporaryDirectory(prefix='n1_') as td:
        for name in want:
            p = files.get(name)
            if p is None:
                print(f'（没有 {name}）')
                continue
            pr, _ = IncrementalRenPyAnalyzer().analyze(files=[p], graph=None)
            beats = beats_from_parse_result(pr)
            xl = Path(td) / f'{p.stem}.xlsx'
            export_beats_to_excel(beats, xl)
            back = import_beats_from_excel(xl)
            again_pr, _ = IncrementalRenPyAnalyzer().analyze(
                files=[_write(Path(td), p.stem, render_beats_to_rpy(back))],
                graph=None)
            again = beats_from_parse_result(again_pr)
            a, b = _paths(beats), _paths(again)
            print('=' * 74)
            print(f'{name}: 原文 {len(beats)} 拍 / 产物再解析 {len(again)} 拍')
            if a == b:
                print('  N1 一致')
                continue
            for key in sorted(set(a) | set(b)):
                if a[key] == b[key]:
                    continue
                print(f'  路径 {key!r}: 原文 {a[key]} 条 / 产物 {b[key]} 条')
                for tag, beats_, cnt in (('原文', beats, a[key]),
                                         ('产物', again, b[key])):
                    shown = 0
                    for bt in beats_:
                        k = f'{getattr(bt, "label", "")}|' \
                            f'{getattr(bt, "nest_path", None)}'
                        if k != key or shown >= cnt:
                            continue
                        shown += 1
                        print(f'      {tag}: kind={bt.kind:<12} '
                              f'cond={(bt.condition or "").strip()!r:<18} '
                              f'| {_what(bt)}')
            # 逐条对照（按渲染出的行找"同一张拍"）
            print('  --- 按产物行对齐看"哪一条多/少" ---')
            src_lines = [ln.strip() for ln in render_beats_to_rpy(beats)
                         if ln.strip()]
            bak_lines = [ln.strip() for ln in render_beats_to_rpy(back)
                         if ln.strip()]
            import difflib
            for ln in difflib.unified_diff(src_lines, bak_lines, lineterm='',
                                           n=1):
                print(f'    {ln}')
    return 0


def _write(d: Path, stem: str, text: str) -> Path:
    f = d / f'{stem}.again.rpy'
    f.write_text(text, encoding='utf-8')
    return f


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
