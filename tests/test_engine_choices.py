"""
判据 · **E2 候选接入编辑器**（2026-10-10）
==========================================

E2 的一句话目标：**"下拉里放什么"由引擎决定，人一个都不用填。**

所以这里要钉住三件事：

1. **引擎优先**：采过 `engine_facts.json` → 分组与值都从引擎来
   （组 = `layeredimage` 的 group，值 = 引擎枚举的属性）；
2. **定义表降级为覆盖层**：项目声明的 `attr_groups` **只用来改中文名**
   （`outfit` → 「服装」），不再指定"哪些词属于哪组"；
3. **没采过就一字不变**：`facts` 为空 → 走原来的老路（这是通用性底线，
   也是"没采到 ≠ 引擎什么都没给"那条规矩的落地）。

另外带上 **N3**（同组冲突工具自查）与 **N2/N5**（覆盖内置语句要报）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.app import beats                                      # noqa: E402
from storygraph.app.session_graph import GraphSessionMixin            # noqa: E402
from storygraph.core import engine_facts as EF                        # noqa: E402


def _facts(attrs, candidates):
    return EF.build_facts({
        'attributes': [(EF.SRC_PROBE, attrs)],
        'candidates': [(EF.SRC_PROBE, candidates)],
    })


LAN_ATTRS = {
    'lan': [{'group': 'expressions', 'attribute': 'happy'},
            {'group': 'expressions', 'attribute': 'sad'},
            {'group': 'outfit', 'attribute': 'clothes2'},
            {'group': 'outfit', 'attribute': 'base_hand_up'}],
}
#: 引擎给的顺序就是"适合展示"的顺序 —— 注意 `outfit` 的词排在 `sad` 前面
LAN_CANDS = {'lan': ['happy', 'clothes2', 'base_hand_up', 'sad', 'zero_shot']}


class AttrGroup:
    """项目声明的词组（与 `authoring.spec_groups.AttrGroup` 同形）。"""

    def __init__(self, name, label, options=()):
        self.name, self.label, self.options = name, label, list(options)

    def title(self):
        return self.label or self.name or '（未命名）'


_Group = AttrGroup          # 用例里的旧名字，保持可读


# ---------------------------------------------------------------------------
#  纯层：分组候选
# ---------------------------------------------------------------------------

class TestGroupedChoices:
    def test_groups_come_from_engine(self):
        got = EF.grouped_choices(_facts(LAN_ATTRS, LAN_CANDS), 'lan')
        names = [g['group'] for g in got]
        assert names[:2] == ['expressions', 'outfit'], got

    def test_values_keep_engine_order(self):
        got = EF.grouped_choices(_facts(LAN_ATTRS, LAN_CANDS), 'lan')
        outfit = next(g for g in got if g['group'] == 'outfit')
        assert outfit['values'] == ['clothes2', 'base_hand_up']

    def test_untagged_candidates_are_not_lost(self):
        """引擎有、但没归到组的候选 → 兜底一栏，**绝不丢词**。"""
        got = EF.grouped_choices(_facts(LAN_ATTRS, LAN_CANDS), 'lan')
        rest = [g for g in got if g['group'] == '']
        assert rest and rest[0]['values'] == ['zero_shot']

    def test_rename_only_changes_the_label(self):
        got = EF.grouped_choices(_facts(LAN_ATTRS, LAN_CANDS), 'lan',
                                 {'outfit': '服装'})
        outfit = next(g for g in got if g['group'] == 'outfit')
        assert outfit['label'] == '服装'
        assert outfit['values'] == ['clothes2', 'base_hand_up']   # 结构没被改

    def test_unknown_tag_is_empty(self):
        assert EF.grouped_choices(_facts(LAN_ATTRS, LAN_CANDS), 'nobody') == []


class TestRenameOverlay:
    def test_reads_objects_and_dicts(self):
        got = EF.rename_overlay([_Group('outfit', '服装'),
                                 {'name': 'expressions', 'label': '表情'},
                                 {'name': 'x', 'label': ''}])
        assert got == {'outfit': '服装', 'expressions': '表情'}

    def test_options_are_ignored(self):
        """归并语义不再走这条路 —— 结构已经是引擎的事实。"""
        got = EF.rename_overlay([_Group('outfit', '服装', ['swim_*'])])
        assert got == {'outfit': '服装'}


# ---------------------------------------------------------------------------
#  纯层：N3 同组冲突
# ---------------------------------------------------------------------------

class TestGroupConflicts:
    def test_two_in_one_group_conflicts(self):
        got = EF.group_conflicts(_facts(LAN_ATTRS, LAN_CANDS), 'lan',
                                 ['happy', 'sad'])
        assert got == [{'group': 'expressions', 'attrs': ['happy', 'sad']}]

    def test_different_groups_are_fine(self):
        assert EF.group_conflicts(_facts(LAN_ATTRS, LAN_CANDS), 'lan',
                                  ['happy', 'clothes2']) == []

    def test_single_attr_is_fine(self):
        assert EF.group_conflicts(_facts(LAN_ATTRS, LAN_CANDS), 'lan',
                                  ['happy']) == []

    def test_unknown_tag_is_empty(self):
        assert EF.group_conflicts(_facts(LAN_ATTRS, LAN_CANDS), 'nobody',
                                  ['a', 'b']) == []


# ---------------------------------------------------------------------------
#  app 层：引擎优先
# ---------------------------------------------------------------------------

class TestExpressionGroupsFor:
    def test_engine_first_does_not_need_symbols(self):
        """引擎事实给了，就不该再要求符号表 —— 编剧一个都不用填。"""
        got = beats.expression_groups_for('lan', None, [], _facts(LAN_ATTRS,
                                                                 LAN_CANDS))
        assert [g['group'] for g in got][:2] == ['expressions', 'outfit']

    def test_engine_first_ignores_declared_options(self):
        """项目声明里的 `options`（旧机制）不再决定结构，只改说法。"""
        got = beats.expression_groups_for(
            'lan', None, [_Group('outfit', '服装', ['swim_*'])],
            _facts(LAN_ATTRS, LAN_CANDS))
        outfit = next(g for g in got if g['group'] == 'outfit')
        assert outfit['label'] == '服装'
        assert outfit['values'] == ['clothes2', 'base_hand_up']

    def test_without_facts_the_old_path_is_untouched(self):
        """**通用性底线**：没采过 → 原来的行为一字不变（空声明仍返回 `[]`）。"""
        assert beats.expression_groups_for('lan', None, [], None) == []

    def test_falls_back_when_engine_has_no_groups_for_tag(self):
        facts = _facts({'other': [{'group': 'g', 'attribute': 'x'}]},
                       {'other': ['x']})
        # 引擎没给 lan 的分组 → 走项目声明（这里是空）→ 空
        assert beats.expression_groups_for('lan', None, [], facts) == []


class _Sym:
    """最小符号表：只提供 `attrs_of_tag`。"""

    def __init__(self, table):
        self._t = table

    def attrs_of_tag(self, tag):
        return list(self._t.get(tag) or [])


class TestOldPathStillWorks:
    def test_declared_groups_still_group(self):
        sym = _Sym({'lan': ['happy', 'sad', 'clothes2']})
        got = beats.expression_groups_for('lan', sym, [_Group('outfit', '服装',
                                                             ['clothes2'])])
        labels = [g['label'] for g in got]
        assert '服装' in labels


class TestBeatGroupConflicts:
    def _beat(self, show):
        b = beats.Beat()
        b.show = show
        return b

    def test_detects_same_group_selection(self):
        """实测 lint 对这类冲突**一声不吭**（`show lan happy sad` → exit 0），
        所以必须由工具自己算。"""
        got = beats.beat_group_conflicts(self._beat('lan happy sad'),
                                         _facts(LAN_ATTRS, LAN_CANDS))
        assert got and got[0]['tag'] == 'lan'
        assert set(got[0]['attrs']) == {'happy', 'sad'}

    def test_no_conflict_across_groups(self):
        assert beats.beat_group_conflicts(self._beat('lan happy clothes2'),
                                          _facts(LAN_ATTRS, LAN_CANDS)) == []

    def test_without_facts_nothing_is_claimed(self):
        assert beats.beat_group_conflicts(self._beat('lan happy sad'), {}) == []


# ---------------------------------------------------------------------------
#  会话：引擎事实的读法（含"采不到 ≠ 没有"）
# ---------------------------------------------------------------------------

class _Stub(GraphSessionMixin):
    """
    只借 `GraphSessionMixin` 的这几个方法 —— 它们只用到 `project_root`。
    继承（而不是鸭子类型）是因为 `engine_facts_warnings` 内部会调 `self.engine_facts()`。
    """

    def __init__(self, root):
        self.project_root = root


def _write_facts(root, **collection):
    facts = EF.build_facts({'images': [(EF.SRC_PROBE, ['lan'])]},
                           collection={'parse_clean': True,
                                       'probe_complete': True,
                                       **collection})
    EF.facts_path(root).write_text(EF.dumps(facts), encoding='utf-8')
    return facts


class TestCardNote:
    """卡片底部那条提示 —— **文案由语义层拼**（前端只显示），所以这里能纯测。"""

    def _beat(self, show, speaker='lyj', text=''):
        b = beats.Beat()
        b.show = show
        b.speaker = speaker
        b.text = text
        return b

    def test_same_group_conflict_is_in_the_note(self):
        note = beats.card_note(self._beat('lan happy sad'), None,
                               _facts(LAN_ATTRS, LAN_CANDS))
        assert 'expressions' in note
        assert 'happy' in note and 'sad' in note

    def test_no_conflict_no_group_mention(self):
        note = beats.card_note(self._beat('lan happy clothes2'), None,
                               _facts(LAN_ATTRS, LAN_CANDS))
        assert 'expressions' not in note and 'outfit' not in note

    def test_without_facts_nothing_is_claimed(self):
        note = beats.card_note(self._beat('lan happy sad'), None, None)
        assert '组里同时选了' not in note

    def test_parts_are_joined_with_a_separator(self):
        """多句时用 `；` 连成一行（`set_note` 是一行 QLabel）。"""
        note = beats.card_note(self._beat('lan happy sad'), None,
                               _facts(LAN_ATTRS, LAN_CANDS))
        assert '\n' not in note


class TestSessionEngineFacts:
    def test_missing_file_is_empty(self, tmp_path):
        assert GraphSessionMixin.engine_facts(_Stub(tmp_path)) == {}

    def test_uncollectable_file_is_empty(self, tmp_path):
        """**坑 4**：采集不可用的那份不能拿来当依据。"""
        _write_facts(tmp_path, probe_complete=False)
        assert GraphSessionMixin.engine_facts(_Stub(tmp_path)) == {}

    def test_valid_file_is_read_and_cached(self, tmp_path):
        _write_facts(tmp_path)
        stub = _Stub(tmp_path)
        first = GraphSessionMixin.engine_facts(stub)
        assert first and EF.value_of(first, 'images') == ['lan']
        assert GraphSessionMixin.engine_facts(stub) is first     # 命中缓存

    def test_cache_invalidates_when_file_changes(self, tmp_path):
        _write_facts(tmp_path)
        stub = _Stub(tmp_path)
        GraphSessionMixin.engine_facts(stub)
        path = EF.facts_path(tmp_path)
        facts = EF.build_facts({'images': [(EF.SRC_PROBE, ['lan', 'ye'])],
                                'labels': [(EF.SRC_PROBE, ['start'])]},
                               collection={'parse_clean': True,
                                           'probe_complete': True})
        # 确保 mtime 变化（同一纳秒内写入会命中旧缓存）
        path.write_text(EF.dumps(facts), encoding='utf-8')
        stat = path.stat()
        import os
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10 ** 9))
        again = GraphSessionMixin.engine_facts(stub)
        assert EF.value_of(again, 'labels') == ['start']

    def test_broken_json_is_empty_not_crash(self, tmp_path):
        EF.facts_path(tmp_path).write_text('{ not json', encoding='utf-8')
        assert GraphSessionMixin.engine_facts(_Stub(tmp_path)) == {}


class TestSessionWarnings:
    def test_overridden_builtin_is_reported(self, tmp_path):
        facts = EF.build_facts({}, collection={'parse_clean': True,
                                              'probe_complete': True},
                               derived={'overridden_builtins': [
                                   {'name': 'pause', 'file': 'aa.rpy',
                                    'line': 3, 'reason': '内置'}]})
        EF.facts_path(tmp_path).write_text(EF.dumps(facts), encoding='utf-8')
        warnings = GraphSessionMixin.engine_facts_warnings(_Stub(tmp_path))
        assert warnings and 'pause' in warnings[0]

    def test_no_facts_no_warnings(self, tmp_path):
        assert GraphSessionMixin.engine_facts_warnings(_Stub(tmp_path)) == []


# ---------------------------------------------------------------------------
#  接进真实 JSON：真机事实的形状能被 E2 消费
# ---------------------------------------------------------------------------

REAL = Path(__import__('os').environ.get(
    'STORYGRAPH_REAL_PROJECT', r'D:\千连万理\RenPyTest\sr_general'))


class TestAgainstRealFacts:
    """拿真机采出来的 `engine_facts.json` 当输入 —— 没有就 skip。"""

    def _load(self):
        path = EF.facts_path(REAL)
        if not path.is_file():
            pytest.skip(f'没有真机事实文件：{path}（先跑 cli engine-facts）')
        return json.loads(path.read_text(encoding='utf-8'))

    def test_real_tag_groups_are_consumable(self):
        facts = self._load()
        assert EF.attributes_of(facts, 'lan'), '真机 lan 应当有属性'
        got = EF.grouped_choices(facts, 'lan', {'outfit': '服装'})
        labels = {g['label'] for g in got}
        assert '服装' in labels, labels
        outfit = next(g for g in got if g['group'] == 'outfit')
        assert 'clothes2' in outfit['values']

    def test_real_beat_produces_display_values(self):
        facts = self._load()
        got = beats.expression_groups_for('lan', None, [], facts)
        assert got, '真机 lan 应当能直接给出分组候选'
        total = sum(len(g['values']) for g in got)
        assert total >= 5, total
