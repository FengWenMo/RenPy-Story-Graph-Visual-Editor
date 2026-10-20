"""看 `nest_path` 长什么样（结构位已是唯一结构真源，2026-10-19 收口）。"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REAL = Path(r'D:\千连万理\RenPyTest\sr_general')

SRC = '''label a:
    e "顶层"
    if outer:
        menu:
            "选项 A":
                e "体 1"
                if flag:
                    e "更深"
            "选项 B":
                e "体 2"
    e "收尾"
'''


def main() -> int:
    from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
    from storygraph.story.beat_build import beats_from_parse_result
    from storygraph.story.beat_render import render_beats_to_rpy

    with tempfile.TemporaryDirectory(prefix='nestpath_') as td:
        f = Path(td) / 'ch.rpy'
        f.write_text(SRC, encoding='utf-8')
        pr, _ = IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)
        beats = beats_from_parse_result(pr)
        print('=== nest_path（解析器写的）===')
        for b in beats:
            print(f'  {getattr(b, "nest_path", None)!r:<28} '
                  f'{b.kind:<12} {(b.text or b.raw or "")[:24]!r}')
        print('\n=== 往返（.rpy → 拍 → xlsx → 拍）之后结构位还在吗 ===')
        from storygraph.story.excel_export import export_beats_to_excel
        from storygraph.story.excel_import import import_beats_from_excel
        xl = Path(td) / 'ch.xlsx'
        export_beats_to_excel(beats, xl)
        back = import_beats_from_excel(xl)
        print(f'  读回 {len(back)} 拍；结构位：'
              f'{[getattr(b, "nest_path", None) for b in back]}')
        print(f'  产物结构（渲染一次再看，结构位说了算）:\n'
              + '\n'.join('    ' + ln for ln in
                          render_beats_to_rpy(back).splitlines()[:12]))

    if REAL.exists():
        print('\n=== 真实项目：结构位齐不齐 ===')
        files = sorted(x for x in (REAL / 'game').rglob('*.rpy')
                       if not x.name.endswith('.generated.rpy'))
        no_info = {}
        for f in files:
            pr, _ = IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)
            bs = beats_from_parse_result(pr)
            if not bs:
                continue
            none_beats = [b for b in bs
                          if getattr(b, 'nest_path', None) is None]
            if none_beats:
                # 只有"**有源码行号**却仍没有结构位"的拍才算问题：
                # 携带拍（`label=''`、文件级块）与"空 label 占位拍"本来就没有
                # 对应的源语句，那是设计如此。
                odd = [b for b in none_beats if getattr(b, 'src_first', 0)]
                if odd:
                    no_info[f.name] = [(b.kind, b.label, b.src_first)
                                       for b in odd[:3]]
        print(f'  文件 {len(files)}；"有行号却没结构位"的 {len(no_info)} 个')
        for name, detail in list(no_info.items())[:6]:
            print(f'    ? 没结构位 {name}: {detail}')
        if not no_info:
            print('  → 结构位齐全（渲染直接按路径建树）')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
