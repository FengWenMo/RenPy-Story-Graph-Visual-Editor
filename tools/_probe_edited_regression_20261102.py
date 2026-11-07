# -*- coding: utf-8 -*-
"""复现金标里 `chapter3_4_4 [edited]` 的 `used_patch: True → False`。

跑法：
    D:\\Python\\python.exe tools/_probe_edited_regression_20261102.py
"""
from __future__ import annotations

import sys

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

REAL = Path(r'D:\千连万理\RenpyTest\sr_general')
NAME = 'chapter3_4_4.rpy'


def main() -> int:
    from storygraph.parsing.discover import discover_scripts
    from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
    from storygraph.pipeline.compile import compile_beats, merge_incrementally
    from storygraph.story.beat_build import beats_from_parse_result
    from storygraph.story.excel_export import export_beats_to_excel
    from storygraph.story.excel_import import import_beats_from_excel

    src = next(f for f in sorted(discover_scripts(REAL))
               if Path(f).name == NAME)
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[src], graph=None)
    beats = beats_from_parse_result(pr)

    # ② via_excel：过一遍表
    import tempfile
    td = Path(tempfile.mkdtemp(prefix='sged_'))
    xlsx = td / 'x.xlsx'
    export_beats_to_excel(beats, xlsx)
    back = import_beats_from_excel(xlsx)

    # ③ edited：每 7 条改一次文案
    n_edit = 0
    for j, b in enumerate(back):
        if (j % 7 == 3 and getattr(b, 'kind', '') in ('say', 'narration')
                and getattr(b, 'text', '')):
            b.text = b.text + 'X'
            n_edit += 1
    print(f'改了 {n_edit} 条文案（共 {len(back)} 拍）')

    gen = compile_beats(back).rpy_text
    res = merge_incrementally(back, gen, source_path=src)
    print(f'used_patch = {res.used_patch}')
    for k, v in sorted(vars(res).items()):
        if k in ('text',):
            print(f'  {k} = <{len(v or "")} 字符>')
        else:
            print(f'  {k} = {v!r}')
    return 0


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
