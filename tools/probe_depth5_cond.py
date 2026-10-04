"""
`depth5_imimi`（5 层混合嵌套）N2 不动点失败的取证：条件到底是**怎么越滚越长**的。

打三样东西，缺一样都只能猜：
  · 每一轮的**拍条件**（`Beat.condition`）与 `nest_path`；
  · 每一轮的**产物行**；
  · `strip_condition_context(cond, context)` 在关键时刻收到的两个入参
    与返回值（直接调它，别从产物反推）。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

KINDS = ['if', 'menu', 'if', 'menu', 'if']


def _beats(text: str, path: Path):
    from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
    from storygraph.story.beat_build import beats_from_parse_result
    path.write_text(text, encoding='utf-8')
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[path], graph=None)
    return beats_from_parse_result(pr)


def _show(tag: str, beats: list) -> None:
    print(f'--- {tag} 拍条件 ---')
    for i, b in enumerate(beats):
        cond = (b.condition or '').strip()
        if cond or b.kind in ('menu', 'menu_option') or 'if:' in (b.nest_path or ''):
            print(f'  [{i:>2}] {b.kind:<12} nest={b.nest_path!r:<22} '
                  f'cond={cond!r}')


def main() -> int:
    import tempfile
    from storygraph.story.beat_render import (render_beats_to_rpy,
                                              strip_condition_context)
    from storygraph.story.excel_export import export_beats_to_excel
    from storygraph.story.excel_import import import_beats_from_excel
    sys.path.insert(0, str(ROOT / 'tools'))
    from hell_nest_inline import _body                     # noqa: E402

    src = 'label t:\n' + '\n'.join(_body(0, KINDS, '    ', tag='5imimi_')) + '\n'
    with tempfile.TemporaryDirectory(prefix='d5_') as td:
        tmp = Path(td)
        b0 = _beats(src, tmp / 'a.rpy')
        r1 = render_beats_to_rpy(b0)
        _show('R0（原文）', b0)
        print('--- R1 ---')
        for ln in r1.splitlines():
            if ln.strip() and not ln.startswith('#'):
                print(f'  |{ln}')

        xl = tmp / 'a.xlsx'
        export_beats_to_excel(b0, xl)
        b1 = import_beats_from_excel(xl)
        r2 = render_beats_to_rpy(b1)
        _show('R2（过表）', b1)
        print('--- R2 ---')
        for ln in r2.splitlines():
            if ln.strip() and not ln.startswith('#'):
                print(f'  |{ln}')

        b2 = _beats(r2, tmp / 'b.rpy')
        r3 = render_beats_to_rpy(b2)
        _show('R3（R2 再解析）', b2)
        print('--- R3 ---')
        for ln in r3.splitlines():
            if ln.strip() and not ln.startswith('#'):
                print(f'  |{ln}')

        print('--- strip_condition_context 直接试 ---')
        for cond, ctx in (
            ('((flag0) and (flag2)) and (flag4)',
             '(flag0) and (flag2)'),
            ('((flag0) and (flag2)) and (((flag0) and (flag2)) and (flag4))',
             '((flag0) and (flag2)) and (flag2)'),
            ('((flag0) and (flag2)) and (flag4)', ''),
            ('(flag0) and (flag2) and (flag4)', '(flag0) and (flag2)'),
        ):
            print(f'  strip({cond!r},\n        {ctx!r}) = '
                  f'{strip_condition_context(cond, ctx)!r}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
