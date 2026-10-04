"""
看 C 的效果：行尾注释在**全量**（表那条路）还在不在，以及会不会贴错行。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SRC = '''label a:
    e "顶层"                       # 行尾注释：顶层台词
    menu:
        "选项 A":                  # 行尾注释：菜单选项
            e "选项体"              # 行尾注释：选项体里
            if flag:               # 行尾注释：条件行
                e "深层"
    jump other                     # 行尾注释：跳转
    stop                           # 行尾注释：停
'''

REAL = Path(r'D:\千连万理\RenPyTest\sr_general')


def main() -> int:
    from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
    from storygraph.story.beat_build import beats_from_parse_result
    from storygraph.story.beat_render import render_beats_to_rpy
    from storygraph.story.excel_export import export_beats_to_excel
    from storygraph.story.excel_import import import_beats_from_excel

    with tempfile.TemporaryDirectory(prefix='c_') as td:
        tmp = Path(td)
        f = tmp / 'ch.rpy'
        f.write_text(SRC, encoding='utf-8')
        pr, _ = IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)
        beats = beats_from_parse_result(pr)
        print('=== 拍上的行尾注释（解析出来的）===')
        for b in beats:
            note = getattr(b, 'inline_comment', '') or ''
            print(f'  {b.kind:<12} src={b.src_first}-{b.src_last} '
                  f'{(b.text or b.raw or "")[:14]!r:<18} note={note!r}')

        print('\n=== 直接渲染 ===')
        for ln in render_beats_to_rpy(beats, label_name='a').splitlines():
            print(f'  {ln}')
        xl = tmp / 'ch.xlsx'
        export_beats_to_excel(beats, xl)
        back = import_beats_from_excel(xl)
        print('\n=== 过表渲染（全量那条路）===')
        for ln in render_beats_to_rpy(back).splitlines():
            print(f'  {ln}')

    if REAL.exists():
        print('\n=== 真实项目：label 里的行尾注释现在保住多少 ===')
        files = sorted(x for x in (REAL / 'game').rglob('*.rpy')
                       if not x.name.endswith('.generated.rpy'))
        want = got = 0
        detail = []
        for x in files:
            src = x.read_text(encoding='utf-8', errors='replace')
            pr, _ = IncrementalRenPyAnalyzer().analyze(files=[x], graph=None)
            bs = beats_from_parse_result(pr)
            if not bs:
                continue
            out = render_beats_to_rpy(bs)
            notes = [getattr(b, 'inline_comment', '') for b in bs
                     if getattr(b, 'inline_comment', '')]
            if not notes:
                continue
            kept = sum(1 for n in notes if n in out)
            want += len(notes)
            got += kept
            if kept != len(notes):
                detail.append((x.name, len(notes) - kept, len(notes)))
        print(f'  带注释的拍 {want} 个 → 产物里保住 {got} 个')
        for name, miss, total in detail[:6]:
            print(f'    {name}: 丢 {miss}/{total}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
