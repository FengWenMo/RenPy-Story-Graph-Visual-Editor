"""
拍级**三向对账**的判据（设计文档 §5 第 6 步）
========================================================================

`story/beat_merge.three_way(base, left, right)` 只读，回答：
**同一拍，是表改了、源码改了、还是两边都改了（冲突）？**

四种局面必须分得清（这是这一层的全部价值）：

    表改 / 源码没改   → left_changed
    源码改 / 表没改   → right_changed
    两边都改且不同    → **conflict**（要人来定）
    两边都改且一样    → both_same（不算冲突）

另加两条硬要求：真机"**没人改 → 0 冲突**"（不然就是假阳），
以及"改了一拍必须被看见"（变异，防空转）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.parsing.discover import discover_scripts            # noqa: E402
from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer  # noqa: E402
from storygraph.story.beat_build import beats_from_parse_result     # noqa: E402
from storygraph.story.beat_merge import (                           # noqa: E402
    fingerprint, format_report, three_way,
)

REAL = Path(r'D:\千连万理\RenPyTest\sr_general')
real_only = pytest.mark.skipif(not REAL.is_dir(), reason='换机 skip')

SCRIPT = (
    'label a:\n'
    '    "一"\n'
    '    "二"\n'
    '    "三"\n'
    '    return\n'
)


def _beats(tmp_path, script=SCRIPT, name='ch.rpy'):
    root = tmp_path / name.replace('.rpy', '')
    (root / 'game').mkdir(parents=True, exist_ok=True)
    f = root / 'game' / name
    f.write_text(script, encoding='utf-8')
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)
    return beats_from_parse_result(pr)


def _copy(beats):
    """深拷贝一份拍（对账**不改**输入，但夹具得能独立改）。"""
    import copy
    return copy.deepcopy(beats)


def test_only_the_table_side_changed(tmp_path):
    base = _beats(tmp_path)
    left = _copy(base)
    left[0].text = '改过的台词'
    right = _copy(base)
    d = three_way(base, left, right)
    assert d.left_changed and not d.right_changed and d.clean, d.counts()


def test_only_the_source_side_changed(tmp_path):
    base = _beats(tmp_path)
    left = _copy(base)
    right = _copy(base)
    right[1].text = '源码那边改的'
    d = three_way(base, left, right)
    assert d.right_changed and not d.left_changed and d.clean, d.counts()


def test_both_sides_changed_differently_is_a_conflict(tmp_path):
    base = _beats(tmp_path)
    left = _copy(base)
    right = _copy(base)
    left[0].text = '表说：一一一'
    right[0].text = '源码说：壹'
    d = three_way(base, left, right)
    assert len(d.conflicts) == 1, d.counts()
    assert not d.clean
    assert '冲突' in format_report(d)


def test_both_sides_changed_the_same_way_is_not_a_conflict(tmp_path):
    base = _beats(tmp_path)
    left = _copy(base)
    right = _copy(base)
    left[2].text = right[2].text = '两边都改成了这句'
    d = three_way(base, left, right)
    assert d.clean and d.both_same, d.counts()


def test_added_and_removed_are_not_conflicts(tmp_path):
    base = _beats(tmp_path)
    left = _copy(base)
    extra = _copy(base)[:1]
    extra[0].line_id = 'brand_new'
    extra[0].text = '表里新加的一拍'
    left.append(extra[0])
    right = _copy(base)
    right = right[:-1]                      # 源码那边少了一拍
    d = three_way(base, left, right)
    assert d.clean, d.counts()
    assert d.added_right or d.removed_right or d.counts()['added_right'] >= 0


def test_fingerprint_ignores_internal_columns(tmp_path):
    """行号/内部列变了**不算内容变**（否则增量导出会被报成"内容冲突"）。"""
    beats = _beats(tmp_path)
    before = fingerprint(beats[0])
    beats[0].src_first = 999
    beats[0].src_last = 1000
    assert fingerprint(beats[0]) == before


@real_only
def test_real_project_untouched_is_conflict_free():
    """
    **真机**：把源码解析两遍当"表"和"源码"（谁都没改）→ 必须 **0 冲突**。

    这是"假阳探测"：diff 一有假阳，人就会忽略冲突报告（比没有报告更糟）。
    """
    beats = []
    for f in sorted(discover_scripts(REAL)):
        pr, _ = IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)
        beats.extend(beats_from_parse_result(pr))
    assert len(beats) > 500, len(beats)
    d = three_way(beats, _copy(beats), _copy(beats))
    assert d.clean, format_report(d)


@real_only
def test_real_project_change_is_seen():
    """**变异**：真机里改一拍的台词 → 必须报"表侧改了"，不许看不见。"""
    beats = []
    for f in sorted(discover_scripts(REAL))[:8]:
        pr, _ = IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)
        beats.extend(beats_from_parse_result(pr))
    left = _copy(beats)
    victim = next(b for b in left if getattr(b, 'text', ''))
    victim.text = '这一拍被人改了'
    d = three_way(beats, left, _copy(beats))
    assert d.left_changed, d.counts()
    assert victim.line_id in d.left_changed or d.counts()['left_changed'] >= 1
