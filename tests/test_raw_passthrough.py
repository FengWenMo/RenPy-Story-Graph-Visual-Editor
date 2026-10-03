"""
P2-10：**透传的语句必须逐字保真**（降级率不是"质量指标"）
=========================================================

清单一度把"降级率 5.8%（raw 221 条透传）"当成质量问题。复验后的判断是
**不成立**：那 221 条是 `show screen` / `pause` / `window hide` /
`call screen` 这类"模型没覆盖、但原文里确属演出流"的语句，设计上就是
**原样保存、原样回写**（见 `_parse_statement` 的兜底分支）。

所以真正该守的不是"把它们也建模"（那要改表格列契约、还得配套 UI），
而是**"透传的每一行都不许被改动"** —— 这才是那一档风险的真实形态。
"""

from __future__ import annotations

import collections
import tempfile
from pathlib import Path

import pytest

from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
from storygraph.pipeline.compile import compile_beats
from storygraph.story.beat_build import beats_from_parse_result
from storygraph.story.excel_export import export_beats_to_excel
from storygraph.story.excel_import import import_beats_from_excel

#: 覆盖三种透传来源：`screen` / `unmodeled` / `call_screen`，
#: 外加带参数、裸语句、以及缩进在 `if` 里的形态。
SAMPLES = [
    'show screen about(initial_selected=0)',
    'show screen sr_about_render_capture',
    'hide screen msg_dialogue',
    'pause',
    'pause 0.1',
    'pause 4',
    'window hide',
    'window auto',
    'call screen character_gallery_screen',
    'with Dissolve(0.5)',
]


def _roundtrip(script: str, tmp: Path) -> str:
    src = tmp / 'a.rpy'
    src.write_text(script, encoding='utf-8')
    az = IncrementalRenPyAnalyzer()
    pr, _ = az.analyze(files=[src], graph=None)
    beats = beats_from_parse_result(pr)
    x = tmp / 'a.xlsx'
    export_beats_to_excel(beats, x)
    back = import_beats_from_excel(x)
    return compile_beats(back).rpy_text


@pytest.mark.parametrize('stmt', SAMPLES)
def test_raw_statement_survives_byte_for_byte(stmt, tmp_path):
    """每一条透传语句都必须**逐字**出现在产物里。"""
    script = f'label a:\n    {stmt}\n    "一句"\n    return\n'
    out = _roundtrip(script, tmp_path)
    assert stmt in out, f'{stmt!r} 没有逐字出现在产物里：\n{out}'


def test_raw_statements_keep_their_order(tmp_path):
    """
    透传语句与台词、与 `$` 代码的**相对顺序**不许被重排。

    顺序是演出的一部分：`pause 0.1` 从"立绘出现之后"挪到"之前"，
    演出就变了。
    """
    script = (
        'label a:\n'
        '    show screen hud\n'
        '    "第一句"\n'
        '    pause 0.1\n'
        '    $ x = 1\n'
        '    hide screen hud\n'
        '    "第二句"\n'
        '    return\n'
    )
    out = _roundtrip(script, tmp_path)
    body = [ln.strip() for ln in out.splitlines()
            if ln.strip() and not ln.strip().startswith('#')
            and not ln.strip().startswith('label')
            and ln.strip() != 'return']
    assert body == ['show screen hud', '"第一句"', 'pause 0.1', '$ x = 1',
                    'hide screen hud', '"第二句"'], out


def test_raw_kind_is_recorded_for_diagnosis(tmp_path):
    """
    `raw_kind` 是**诊断标签**，必须保留下来。

    没有它，"降级率"就只剩一个数字，分不清是 `show screen`（设计内）
    还是真被漏掉的语句（缺陷）。
    """
    script = (
        'label a:\n'
        '    show screen hud\n'
        '    pause 0.1\n'
        '    call screen picker\n'
        '    "一句"\n'
        '    return\n'
    )
    src = tmp_path / 'a.rpy'
    src.write_text(script, encoding='utf-8')
    az = IncrementalRenPyAnalyzer()
    pr, _ = az.analyze(files=[src], graph=None)
    kinds = collections.Counter(
        (s.payload or {}).get('raw_kind', '')
        for s in pr.statements if s.kind == 'raw')
    assert kinds.get('screen'), kinds
    assert kinds.get('unmodeled'), kinds
    assert kinds.get('call_screen'), kinds


def test_indented_raw_inside_if_survives(tmp_path):
    """缩进在 `if` 里的透传语句也要保住**条件**（不能被提到块外）。"""
    script = (
        'label a:\n'
        '    if flag:\n'
        '        pause 0.1\n'
        '    "一句"\n'
        '    return\n'
    )
    out = _roundtrip(script, tmp_path)
    lines = out.splitlines()
    idx = next(i for i, ln in enumerate(lines) if 'pause 0.1' in ln)
    assert lines[idx].startswith(' ' * 8), out
    assert lines[idx - 1].strip() == 'if flag:', out
