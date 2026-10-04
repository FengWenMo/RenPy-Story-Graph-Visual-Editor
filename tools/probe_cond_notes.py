"""验证 (ii)：`if / elif / else` 头的注释进没进产物、过表还在不在。"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SRC = '''label a:
    if is_title:            # 备注：主标题
        e "标题"
    elif is_sub:            # 备注：副标题
        e "副标题"
    else:                   # 备注：普通文本
        e "正文"
'''

REAL = Path(r'D:\千连万理\RenPyTest\sr_general')


def main() -> int:
    from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
    from storygraph.story.beat_build import beats_from_parse_result
    from storygraph.story.beat_render import render_beats_to_rpy
    from storygraph.story.excel_export import export_beats_to_excel
    from storygraph.story.excel_import import import_beats_from_excel

    with tempfile.TemporaryDirectory(prefix='cond_') as td:
        tmp = Path(td)
        f = tmp / 'ch.rpy'
        f.write_text(SRC, encoding='utf-8')
        pr, _ = IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)
        beats = beats_from_parse_result(pr)
        print('=== 拍上的两个槽 ===')
        for b in beats:
            print(f'  {(b.text or "")[:10]!r:<14} own={b.inline_comment!r:<12} '
                  f'cond={getattr(b, "cond_comment", "")!r}')
        print('\n=== 直接渲染 ===')
        for ln in render_beats_to_rpy(beats, label_name='a').splitlines():
            print(f'  {ln}')
        xl = tmp / 'ch.xlsx'
        export_beats_to_excel(beats, xl)
        back = import_beats_from_excel(xl)
        print('\n=== 过表渲染 ===')
        for ln in render_beats_to_rpy(back).splitlines():
            print(f'  {ln}')

    if REAL.exists():
        print('\n=== 真实项目：条件行注释保住几个 ===')
        files = sorted(x for x in (REAL / 'game').rglob('*.rpy')
                       if not x.name.endswith('.generated.rpy'))
        kept = total = 0
        for x in files:
            pr, _ = IncrementalRenPyAnalyzer().analyze(files=[x], graph=None)
            bs = beats_from_parse_result(pr)
            if not bs:
                continue
            out = render_beats_to_rpy(bs)
            for b in bs:
                note = getattr(b, 'cond_comment', '') or ''
                if note:
                    total += 1
                    kept += 1 if note in out else 0
        print(f'  条件行注释 {total} 处 → 产物里 {kept} 处')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
