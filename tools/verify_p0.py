#!/usr/bin/env python
"""
验证上一轮评估报出的三个缺陷是否已修复。

  A. menu 块往返后是否还在（P0）
  B. if 条件是否落 Excel（P0）
  C. 台词尾部空格是否保留（P1）

用法：python tools/verify_p0.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer  # noqa
from storygraph.story.beat_build import beats_from_parse_result        # noqa
from storygraph.story.excel_export import export_beats_to_excel        # noqa
from storygraph.story.excel_import import import_beats_from_excel      # noqa
from storygraph.pipeline.compile import compile_beats                  # noqa

CASES = {
    'A. menu 块': (
        'label a:\n'
        '    menu:\n'
        '        "选项A":\n'
        '            jump b\n'
        '        "选项B":\n'
        '            jump c\n',
        lambda out: ('menu:' in out) and ('"选项A"' in out)
        and ('"选项B"' in out),
        '产物应含 menu: 块，且两个选项都在',
    ),
    'B. if 条件': (
        'label a:\n'
        '    if love > 5:\n'
        '        e "喜欢你"\n',
        lambda out: 'if love > 5:' in out,
        '产物应含 if love > 5:',
    ),
    'C. 尾部空格': (
        'label a:\n'
        '    e "元神，启动！ "\n',
        lambda out: '元神，启动！ ' in out,
        '产物应保留台词尾部空格',
    ),
}


def roundtrip(src: str, tmp: Path) -> str:
    f = tmp / 'case.rpy'
    f.write_text(src, encoding='utf-8')
    analyzer = IncrementalRenPyAnalyzer()
    pr, _ = analyzer.analyze(files=[f], graph=None)
    beats = beats_from_parse_result(pr)
    x = tmp / 'case.xlsx'
    export_beats_to_excel(beats, x)
    back = import_beats_from_excel(x)
    return compile_beats(back).rpy_text


def main() -> int:
    print('=== 三个缺陷复验（原文 → Excel → 产物）===\n')
    failed = 0
    for name, (src, check, expect) in CASES.items():
        tmp = Path(tempfile.mkdtemp(prefix='verify_'))
        try:
            out = roundtrip(src, tmp)
        except Exception as e:
            print(f'{name}  异常：{type(e).__name__}: {e}')
            failed += 1
            continue
        ok = check(out)
        print(f'{name}  {"已修复" if ok else "仍未修复"}')
        print(f'  期望：{expect}')
        print('  实际产物：')
        for ln in out.splitlines():
            print(f'    | {ln}')
        if not ok:
            failed += 1
        print()
    print(f'结果：{len(CASES) - failed}/{len(CASES)} 通过')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
