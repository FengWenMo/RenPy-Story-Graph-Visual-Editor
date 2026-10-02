#!/usr/bin/env python
"""复现评估报告里的 P0-1 / P0-2 / P1-3（临时脚本）。"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.parsing.renpy_parser import (  # noqa: E402
    RenPyParser, ParseResultBuilder,
)
from storygraph.story.beat_build import beats_from_statements  # noqa: E402
from storygraph.story.excel_export import export_beats_to_excel  # noqa: E402
from storygraph.story.excel_import import import_beats_from_excel  # noqa: E402
from storygraph.pipeline.compile import compile_beats  # noqa: E402
from storygraph.story.script_table import render_beats_to_rpy  # noqa: E402


MENU_SCRIPT = (
    'label a:\n'
    '    "开场"\n'
    '    menu:\n'
    '        "选项A":\n'
    '            jump b\n'
    '        "选项B":\n'
    '            jump c\n'
    '    return\n'
)

IF_SCRIPT = (
    'label a:\n'
    '    "开场"\n'
    '    if love > 5:\n'
    '        "高好感"\n'
    '    elif love > 2:\n'
    '        "中好感"\n'
    '    else:\n'
    '        "低好感"\n'
    '    "结束"\n'
    '    return\n'
)

TRAILING_SCRIPT = (
    'label a:\n'
    '    e "元神，启动！ "\n'
    '    return\n'
)


def parse_text(text, tmpdir, name):
    p = Path(tmpdir) / name
    p.write_text(text, encoding='utf-8')
    pr = ParseResultBuilder().build(RenPyParser().parse(p))
    return pr


def show(title, text):
    print('=' * 70)
    print(title)
    print('-' * 70)
    print(text)


def main():
    tmp = tempfile.mkdtemp(prefix='repro_')

    # ---------- 直接渲染（不经 Excel）----------
    pr = parse_text(MENU_SCRIPT, tmp, 'menu.rpy')
    beats = beats_from_statements(pr.statements, labels=pr.statement_labels)
    print('beats kinds:', [b.kind for b in beats])
    direct = render_beats_to_rpy(beats, label_name='a')
    show('P0-1a  menu 脚本：直接 render_beats_to_rpy（不经 Excel）', direct)

    # ---------- 经 Excel ----------
    x = Path(tmp) / 'menu.xlsx'
    export_beats_to_excel(beats, x)
    back = import_beats_from_excel(x)
    res = compile_beats(back)
    show('P0-1b  menu 脚本：beats -> xlsx -> beats -> compile', res.rpy_text)

    # ---------- P0-2 condition ----------
    pr2 = parse_text(IF_SCRIPT, tmp, 'iff.rpy')
    print('原文 condition:',
          [s.condition for s in pr2.statements if s.condition])
    beats2 = beats_from_statements(pr2.statements, labels=pr2.statement_labels)
    print('beats condition:', [(b.seq, b.kind, b.text, b.condition)
                               for b in beats2])
    from storygraph.story.script_table import BEAT_FIELDS
    print('BEAT_FIELDS 里有 Condition 列?', 'Condition' in BEAT_FIELDS)
    print('to_row 的 Condition 位:', repr(
        beats2[1].to_row()[BEAT_FIELDS.index('Condition')]))
    x2 = Path(tmp) / 'iff.xlsx'
    export_beats_to_excel(beats2, x2)
    back2 = import_beats_from_excel(x2)
    print('回导后 condition:', [(b.seq, b.kind, b.text, b.condition)
                                for b in back2])
    res2 = compile_beats(back2)
    show('P0-2   if/elif/else：beats -> xlsx -> beats -> compile',
         res2.rpy_text)

    # ---------- P1-3 尾部空格 ----------
    pr3 = parse_text(TRAILING_SCRIPT, tmp, 'trail.rpy')
    beats3 = beats_from_statements(pr3.statements, labels=pr3.statement_labels)
    print('原文 text  :', repr(pr3.statements[0].payload.get('text')))
    print('beat.text  :', repr(beats3[0].text))
    x3 = Path(tmp) / 'trail.xlsx'
    export_beats_to_excel(beats3, x3)
    back3 = import_beats_from_excel(x3)
    print('回导后 text:', repr(back3[0].text))
    res3 = compile_beats(back3)
    show('P1-3   尾部空格：beats -> xlsx -> beats -> compile', res3.rpy_text)


if __name__ == '__main__':
    main()
