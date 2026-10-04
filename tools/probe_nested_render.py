"""
嵌套菜单渲染诊断：记录每一次 `render_menu_block` 的调用（缩进 + 菜单头）
以及每次 `_render_menu_segments` 的分段结果，看嵌套那一层到底被谁渲染。

用法：
    D:/Python/python.exe tools/probe_nested_render.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CASES = {
    '两层（最小复现）': (
        'label t:\n'
        '    menu:\n'
        '        "外":\n'
        '            e "前"\n'
        '            menu:\n'
        '                "内":\n'
        '                    e "深深"\n'
        '            e "后"\n'
    ),
    '三层（menu 套 menu 套 menu）': (
        'label t:\n'
        '    menu:\n'
        '        "外":\n'
        '            e "L1"\n'
        '            menu:\n'
        '                "中":\n'
        '                    e "L2"\n'
        '                    menu:\n'
        '                        "内":\n'
        '                            e "L3"\n'
        '                    e "L2尾"\n'
        '            e "L1尾"\n'
    ),
    'if 夹在中间': (
        'label t:\n'
        '    if a:\n'
        '        menu:\n'
        '            "外":\n'
        '                if b:\n'
        '                    menu:\n'
        '                        "内":\n'
        '                            e "深"\n'
    ),
}


def main() -> int:
    import tempfile

    from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
    from storygraph.story import beat_render as br
    from storygraph.story.beat_build import beats_from_parse_result
    from storygraph.story.excel_export import export_beats_to_excel
    from storygraph.story.excel_import import import_beats_from_excel

    calls: list = []
    real_block = br.render_menu_block
    real_segments = br._menu_segments

    def spy_block(*a, **kw):
        labels = kw.get('label_name')
        indent = kw.get('indent', '')
        run = a[0] if a else kw.get('run')
        head = getattr(run[0], 'text', '') if run else ''
        calls.append(f'render_menu_block(indent={len(indent)} '
                     f'拍数={len(run) if run else 0} 头={head!r:.12})')
        return real_block(*a, **kw)

    def spy_segments(*a, **kw):
        run = a[0] if a else kw.get('run')
        segs = real_segments(*a, **kw)
        if segs is not None:
            for i, (opt, body, foreign) in enumerate(segs):
                calls.append(
                    f'  段{i}: opt={(getattr(opt, "text", None))!r} '
                    f'body={[b.kind for _p, b, _w in body]} '
                    f'foreign={[b.kind for _p, b, _w in foreign]}')
        return segs

    br.render_menu_block = spy_block
    br._menu_segments = spy_segments

    with tempfile.TemporaryDirectory(prefix='nest_render_') as td:
        tmp = Path(td)
        for name, src in CASES.items():
            calls.clear()
            f = tmp / 'ch.rpy'
            f.write_text(src, encoding='utf-8')
            pr, _ = IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)
            beats = beats_from_parse_result(pr)
            print('=' * 72)
            print(f'### {name}')
            print('--- 结构位 ---')
            for b in beats:
                print(f'   {b.kind:<12} {b.nest_path!r:<28} '
                      f'{(b.text or b.raw or "")[:14]!r}')
            r1 = br.render_beats_to_rpy(beats)
            print('--- 调用轨迹（直接渲染）---')
            for c in calls:
                print('  ' + c)
            print('--- R1 ---')
            for ln in r1.splitlines():
                if ln.strip() and not ln.startswith('#'):
                    print(f'  |{ln}')
            xl = tmp / 'ch.xlsx'
            export_beats_to_excel(beats, xl)
            r2 = br.render_beats_to_rpy(import_beats_from_excel(xl))
            print(f'--- R2 与 R1 {"一致 ✓" if r2 == r1 else "不一致 ✗"} ---')
            if r2 != r1:
                for ln in r2.splitlines():
                    if ln.strip() and not ln.startswith('#'):
                        print(f'  |{ln}')
    br.render_menu_block = real_block
    br._menu_segments = real_segments
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
