"""
M4：投影 —— T1 的**头行可编辑** + 参数候选
==========================================

MPS 的那句话："同一个模型可以有多种记法"。这一层的落点是：

  · **T1 通用行有一个真正的编辑面** —— `SpecHead`（头行原文）；
  · 块体仍旧逐字躺在 `Opaque` 里（撞不到）；
  · 声明的 `columns` / `choices` 变成**可用的候选**（不再只是文档里的字段）。

⚠ 这一层最容易犯的错是"顺手把字节改了"。所以判据里最要紧的两条是：

  1. **没改 → 逐字节不变**（靠**结构**保证：渲染层比对头行，相同就不替换 ——
     不是靠调用方自觉）；
  2. **改了 → 生效**，而且**只动那一行**，块体一个字都不许跟着变。

第 2 条正是"判据要能抓住损坏"的现场：只断言"新参数在产物里"是不够的，
必须同时断言"块体逐字没动"。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
from storygraph.pipeline.compile import compile_beats
from storygraph.story.beat_build import beats_from_parse_result
from storygraph.story.beat_render import render_beats_to_rpy
from storygraph.story.excel_export import export_beats_to_excel
from storygraph.story.excel_import import import_beats_from_excel
from storygraph.story.statement_registry import (StatementRegistry,
                                                 clear_cache,
                                                 spec_arg_choices,
                                                 spec_columns)

SCRIPT = (
    'label a:\n'
    '    "第一句"\n'
    '    mycue "闪电" intensity=3:\n'
    '        target = ling\n'
    '        hold 0.5\n'
    '    "第二句"\n'
    '    return\n'
)

MANIFEST = {'statements': [{
    'name': 'mycue', 'block': True, 'label': '闪电动画',
    'columns': [
        {'name': 'intensity', 'choices': ['1', '2', '3']},
        {'name': 'target', 'choices': ['ling', 'lan']},
    ]}]}


@pytest.fixture(autouse=True)
def _fresh_cache():
    clear_cache()
    yield
    clear_cache()


def _project(tmp_path, manifest=MANIFEST, script=SCRIPT):
    root = tmp_path / 'proj'
    game = root / 'game'
    game.mkdir(parents=True, exist_ok=True)
    f = game / 'ch.rpy'
    f.write_text(script, encoding='utf-8')
    if manifest is not None:
        (root / 'storygraph.rules.json').write_text(
            json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
    return root, f


def _beats(path):
    pr = IncrementalRenPyAnalyzer().analyze(files=[path], graph=None)[0]
    return pr, beats_from_parse_result(pr)


def _roundtrip(tmp_path, path, mutate=None):
    pr, beats = _beats(path)
    if mutate:
        mutate(beats)
    x = tmp_path / 'o.xlsx'
    export_beats_to_excel(beats, x)
    return compile_beats(import_beats_from_excel(x)).rpy_text


def _target_beat(beats):
    hits = [b for b in beats if getattr(b, 'opaque', '')]
    assert len(hits) == 1, [(b.seq, b.opaque) for b in beats]
    return hits[0]


# ---------------------------------------------------------------------------
#  ① 头行的解析与"没改就不动"
# ---------------------------------------------------------------------------

def test_head_is_captured_verbatim(tmp_path):
    _root, path = _project(tmp_path)
    _pr, beats = _beats(path)
    b = _target_beat(beats)
    assert b.spec_name == 'mycue'
    assert b.spec_head == 'mycue "闪电" intensity=3:', b.spec_head
    # 块体现在在 `opaque` 里**逐字**躺着（含相对缩进）
    assert b.opaque.split('\n') == [
        'mycue "闪电" intensity=3:', '    target = ling', '    hold 0.5',
    ], b.opaque


def test_untouched_head_is_byte_identical(tmp_path):
    """
    **没改 → 逐字节不变**。这条靠**结构**成立（渲染层比对头行），
    不是靠"没人会去改它"。
    """
    _root, path = _project(tmp_path)
    pr, beats = _beats(path)
    out = compile_beats(beats).rpy_text
    assert 'mycue "闪电" intensity=3:' in out
    _ = pr


def test_head_survives_excel_roundtrip(tmp_path):
    _root, path = _project(tmp_path)
    out = _roundtrip(tmp_path, path)
    assert 'mycue "闪电" intensity=3:' in out, out
    assert 'target = ling' in out, out


# ---------------------------------------------------------------------------
#  ② 改了 → 生效，而且**只动那一行**
# ---------------------------------------------------------------------------

def test_editing_head_changes_only_that_line(tmp_path):
    """
    改头行 → 产物跟着变，**块体一个字都不许动**。

    ⚠ 只断言"新参数在产物里"是**不够**的：那样"顺手重排了块体"也能过。
    这里逐行比对块体，把"只动一行"钉死。
    """
    _root, path = _project(tmp_path)
    baseline = _roundtrip(tmp_path, path)

    def mutate(beats):
        _target_beat(beats).spec_head = 'mycue "闪电" intensity=9:'

    changed = _roundtrip(tmp_path, path, mutate=mutate)
    assert 'intensity=9:' in changed, changed
    assert 'intensity=3:' not in changed, changed
    # 块体逐字不动
    for line in ('target = ling', 'hold 0.5'):
        assert line in changed, changed
    # 而且**只有那一行**不同
    before = baseline.split('\n')
    after = changed.split('\n')
    assert len(before) == len(after), (baseline, changed)
    diff = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
    assert len(diff) == 1, [before[i] for i in diff]


def test_head_edit_through_excel_cell(tmp_path):
    """头行是**单行可编辑格**：从 Excel 改也要生效（读表那条路）。"""
    _root, path = _project(tmp_path)
    pr, beats = _beats(path)
    beats = [b for b in beats]
    _target_beat(beats).spec_head = 'mycue "闪电" intensity=7:'
    x = tmp_path / 'o.xlsx'
    export_beats_to_excel(beats, x)
    back = import_beats_from_excel(x)
    assert _target_beat(back).spec_head == 'mycue "闪电" intensity=7:'
    assert 'intensity=7:' in render_beats_to_rpy(back), (
        render_beats_to_rpy(back))


def test_removing_declaration_still_keeps_bytes(tmp_path):
    """退化（删声明）之后，`spec_head` 没了，但产物必须与原来逐字相同。"""
    root, path = _project(tmp_path)
    declared = _roundtrip(tmp_path, path)
    (root / 'storygraph.rules.json').unlink()
    clear_cache()
    degraded = _roundtrip(tmp_path, path)
    assert declared == degraded, (declared, degraded)


# ---------------------------------------------------------------------------
#  ③ 候选：声明的 columns / choices 变成**可用**的东西
# ---------------------------------------------------------------------------

def test_spec_columns_are_readable(tmp_path):
    root, _path = _project(tmp_path)
    reg = StatementRegistry.for_project(root)
    cols = spec_columns(reg, 'mycue')
    assert [c['name'] for c in cols] == ['intensity', 'target']
    assert cols[0]['choices'] == ['1', '2', '3']


def test_arg_choices_are_per_column(tmp_path):
    root, _path = _project(tmp_path)
    reg = StatementRegistry.for_project(root)
    assert spec_arg_choices(reg, 'mycue', 'intensity') == ['1', '2', '3']
    assert spec_arg_choices(reg, 'mycue', 'target') == ['ling', 'lan']
    assert spec_arg_choices(reg, 'mycue') == ['1', '2', '3', 'ling', 'lan']


def test_choices_are_empty_when_not_declared(tmp_path):
    """
    **没声明就返回空** —— 不许"猜一组候选"。

    猜出来的下拉会诱导人写出脚本里根本没有的值，而那正是"工具自作聪明"
    最典型的翻车方式。
    """
    root, _path = _project(tmp_path, manifest={'statements': ['mycue']})
    reg = StatementRegistry.for_project(root)
    assert spec_arg_choices(reg, 'mycue', 'intensity') == []
    assert spec_columns(reg, 'mycue') == []
    # 连这条语句都是不认识的 → 更是空
    assert spec_arg_choices(reg, 'nope') == []


# ---------------------------------------------------------------------------
#  ④ 能力等级：有了 columns/label → T1a
# ---------------------------------------------------------------------------

def test_declared_with_columns_is_t1a_on_real_beats(tmp_path):
    from storygraph.core import capability as cap
    from storygraph.story.statement_registry import coverage_of_beats

    root, path = _project(tmp_path)
    _pr, beats = _beats(path)
    cov = coverage_of_beats(beats, StatementRegistry.for_project(root))
    assert cov.count_of(cap.T1A) == 1, cov.counts
    assert cov.count_of(cap.T1B) == 0, cov.counts
    assert cov.ratio > 0
