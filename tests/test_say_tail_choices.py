"""
台词后缀的**候选变换表**（2026-11-03）
=====================================

用户："这个纯文本框是必须手打整一个句子……？还是只打个 with 就行……？
逻辑没说明白，好奇怪啊……我觉得还是来个候选变换表吧。"

先说清那个"奇怪"：这一格是**引号之后原样拼回去的整段**，所以要么打整句
`with dissolve`，要么（错误地）只打 `with` → 写出 `e "台词" with`，
引擎直接报错。**没有"只打 with"这种用法** —— 从前的界面既没候选也没说明。

这个文件钉四件事：

  1. 候选来自**项目自己的 `transform`**（不是写死 `dissolve/fade`）——
     真机 155 个 transform 里有 `bounce_quick` / `simple_hologram_effect`
     这种自造的，内置清单会让人以为"只有这几个"；
  2. 第一项是「（无）」= 普通台词；最后一项是 `nointeract`；
  3. 读不到资源表 → 只剩这两项，**手打仍然可用**（可编辑下拉）；
  4. 选/手打的**真值都是整段子句**（`with dissolve` / `(what_color="#f00")
     with dissolve`），不是"只选了 dissolve"。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _symbols(**transforms):
    return SimpleNamespace(registry=SimpleNamespace(transforms=transforms))


class TestChoicesComeFromTheProject:
    def test_first_item_is_none(self):
        from storygraph.ui_qt.card_editor import say_tail_choices
        assert say_tail_choices(_symbols(dissolve={}))[0] == ('（无）', '')

    def test_project_transforms_are_offered_as_bare_names(self):
        """候选是**转场名**（`dissolve`），`with ` 由 `join_say_tail` 补。"""
        from storygraph.ui_qt.card_editor import say_tail_choices
        ch = say_tail_choices(_symbols(dissolve={}, vpunch={}, bounce_quick={}))
        values = [v for _l, v in ch]
        assert 'dissolve' in values
        assert 'vpunch' in values
        assert 'bounce_quick' in values           # 自造的名字也在
        assert not any(v.startswith('with ') for v in values), \
            '候选里不该带 `with` —— 那是显示层的噪音'

    def test_nointeract_is_offered(self):
        from storygraph.ui_qt.card_editor import say_tail_choices
        assert say_tail_choices(_symbols())[-1] == ('nointeract', 'nointeract')

    def test_display_equals_value_for_clauses(self):
        """显示与真值一致：转场名就是用户认识的那串，没必要翻译。"""
        from storygraph.ui_qt.card_editor import say_tail_choices
        for label, value in say_tail_choices(_symbols(dissolve={})):
            if value.startswith('with '):
                assert label == value

    def test_caps_the_list(self):
        """项目 transform 再多也不许把下拉撑成几百项。"""
        from storygraph.ui_qt.card_editor import say_tail_choices
        many = {f't{i:03d}': {} for i in range(500)}
        ch = say_tail_choices(SimpleNamespace(
            registry=SimpleNamespace(transforms=many)))
        assert len(ch) <= 202


class TestDegradesWithoutProject:
    @pytest.mark.parametrize('symbols', [None,
                                         SimpleNamespace(),
                                         SimpleNamespace(registry=None),
                                         SimpleNamespace(
                                             registry=SimpleNamespace())])
    def test_still_usable(self, symbols):
        from storygraph.ui_qt.card_editor import say_tail_choices
        ch = say_tail_choices(symbols)
        assert ch[0] == ('（无）', '')
        assert ('nointeract', 'nointeract') in ch


class TestTheWidgetIsAnEditableCombo:
    """**可编辑**是关键：`(kwargs)` 与组合子句必须能手打进去。"""

    def _editor(self, qapp, say_tail=''):
        from storygraph.ui_qt.card_editor import BeatCardEditor
        ed = BeatCardEditor(symbols=_symbols(dissolve={}, vpunch={}))
        # ⚠ `load_fields` 吃的是**列名**（`SayTail`），不是属性名（`say_tail`）。
        ed.load_fields({'kind': 'say', 'speaker': 'jc', 'text': '台词',
                        'SayTail': say_tail}, [])
        return ed

    def test_it_is_a_combo_and_editable(self, qapp):
        from PySide6.QtWidgets import QComboBox
        ed = self._editor(qapp)
        w = ed._widgets['SayTail']
        assert isinstance(w, QComboBox), '还是文本框'
        assert w.isEditable(), '不可编辑 → 手写 (kwargs) 进不去'

    def test_choosing_a_transform_yields_the_whole_clause(self, qapp):
        """选 `vpunch` → 写回 `with vpunch`（用户不用打 `with`）。"""
        ed = self._editor(qapp)
        w = ed._widgets['SayTail']
        w.setCurrentIndex(w.findText('vpunch'))
        assert ed._get_value('SayTail') == 'with vpunch'

    def test_free_text_survives(self, qapp):
        ed = self._editor(qapp)
        w = ed._widgets['SayTail']
        w.setEditText('(what_color="#f00") with dissolve')
        assert ed._get_value('SayTail') == '(what_color="#f00") with dissolve'

    def test_round_trip_through_load_fields(self, qapp):
        """已有值要能回显（`setEditText` 之后 `currentIndex` 仍指上一项 —— 老坑）。"""
        ed = self._editor(qapp, say_tail='(no_skip=True)')
        assert ed._get_value('SayTail') == '(no_skip=True)'

    def test_choosing_none_clears_it(self, qapp):
        ed = self._editor(qapp, say_tail='with dissolve')
        w = ed._widgets['SayTail']
        w.setCurrentIndex(w.findText('（无）'))
        assert ed._get_value('SayTail') == ''


class TestBuiltinsComeFirst:
    """`with` 该配的是 **transition**，不是任意 transform。

    真机 167 个 transform 里混着 `i11`（位置）/ `ui_hover_nudge`（界面动效）
    / `bounce_duration`（动画）—— 全量平铺是噪声。所以：内置 transition
    在前（引擎标准，任何项目都能用），项目自己的在后（用户自己认）。
    """

    def test_builtins_are_before_project_transforms(self):
        from storygraph.ui_qt.card_editor import (BUILTIN_TRANSITIONS,
                                                  say_tail_choices)
        ch = say_tail_choices(_symbols(aaa_project={}, zzz_project={}))
        values = [v for _l, v in ch]
        last_builtin = max(values.index(n)
                           for n in BUILTIN_TRANSITIONS if n in values)
        first_project = values.index('aaa_project')
        assert last_builtin < first_project, '内置与项目 transform 串了'

    def test_project_transform_duplicating_a_builtin_is_not_repeated(self):
        from storygraph.ui_qt.card_editor import say_tail_choices
        ch = say_tail_choices(_symbols(dissolve={}))
        values = [v for _l, v in ch]
        assert values.count('dissolve') == 1

    def test_none_is_first_and_nointeract_is_last(self):
        from storygraph.ui_qt.card_editor import say_tail_choices
        ch = say_tail_choices(_symbols(dissolve={}))
        assert ch[0] == ('（无）', '')
        assert ch[-1] == ('nointeract', 'nointeract')


class TestItSitsNextToTheTextNotInAdvanced:
    """2026-11-03 用户："with 是 say 的一部分，凭什么要往下翻高级区？"

    这条判据钉的是**位置**：`SayTail` 的控件必须在对话区（`_dialogue_box`），
    不在高级区（`_adv_host`）。两个都改回"只在高级区"就立刻红。
    """

    def _editor(self, qapp):
        from storygraph.ui_qt.card_editor import BeatCardEditor
        ed = BeatCardEditor(symbols=_symbols(dissolve={}, vpunch={}))
        ed.load_fields({'kind': 'say', 'speaker': 'jc', 'text': '台词',
                        'SayTail': ''}, [])
        return ed

    def test_widget_lives_in_the_dialogue_box(self, qapp):
        ed = self._editor(qapp)
        w = ed._widgets['SayTail']
        host = w.parent()
        inside_dialogue = False
        while host is not None:
            if host is ed._dialogue_box:
                inside_dialogue = True
                break
            host = host.parent()
        assert inside_dialogue, '台词转场不在对话区里'

    def test_widget_is_not_in_the_advanced_box(self, qapp):
        ed = self._editor(qapp)
        w = ed._widgets['SayTail']
        host = w.parent()
        while host is not None:
            assert host is not getattr(ed, '_adv_host', None), \
                '台词转场还被塞在高级区里（会建两个控件、值互相覆盖）'
            host = host.parent()

    def test_advanced_box_has_no_duplicate_combo(self, qapp):
        """高级区里不许再出现第二个 SayTail 控件。"""
        from PySide6.QtWidgets import QComboBox
        ed = self._editor(qapp)
        adv = getattr(ed, '_adv_host', None)
        assert adv is not None
        combos = adv.findChildren(QComboBox)
        assert ed._say_tail not in combos


class TestDisplayVersusStoredValue:
    """控件里是**转场名**，写回的是**整段子句** —— 这一层转换单独测。"""

    @pytest.mark.parametrize('stored,shown', [
        ('with dissolve', 'dissolve'),
        ('with my.trans', 'my.trans'),
        ('nointeract', 'nointeract'),
        ('(what_color="#f00") with dissolve',
         '(what_color="#f00") with dissolve'),
        ('', ''),
    ])
    def test_split(self, stored, shown):
        from storygraph.ui_qt.card_editor import split_say_tail
        assert split_say_tail(stored) == shown

    @pytest.mark.parametrize('shown,stored', [
        ('dissolve', 'with dissolve'),
        ('my.trans', 'with my.trans'),
        ('nointeract', 'nointeract'),
        ('(what_color="#f00") with dissolve',
         '(what_color="#f00") with dissolve'),
        ('', ''),
    ])
    def test_join(self, shown, stored):
        from storygraph.ui_qt.card_editor import join_say_tail
        assert join_say_tail(shown) == stored

    @pytest.mark.parametrize('stored', [
        'with dissolve', 'nointeract', '(id="l1")', '(no_skip=True)',
    ])
    def test_split_join_round_trip(self, stored):
        from storygraph.ui_qt.card_editor import (join_say_tail,
                                                  split_say_tail)
        assert join_say_tail(split_say_tail(stored)) == stored

    def test_shown_value_comes_back_as_whole_clause(self, qapp):
        from storygraph.ui_qt.card_editor import BeatCardEditor
        ed = BeatCardEditor(symbols=_symbols())
        ed.load_fields({'kind': 'say', 'text': 't',
                        'SayTail': 'with dissolve'}, [])
        assert ed._say_tail.currentText() == 'dissolve'
        assert ed._get_value('SayTail') == 'with dissolve'


class TestItIsAPrimaryFieldNow:
    """**分组**也在判据里：留在 `ADVANCED_FIELDS` 就等于"高级区一收起就丢值"。

    `collect_fields()` 只在高级区**展开**时才收 `ADVANCED_FIELDS` ——
    所以"挪控件不挪分组"是个假修复：界面上看着在台词下面，收起高级区之后
    `with dissolve` 根本不写回。
    """

    def test_it_is_primary_not_advanced(self):
        from storygraph.app.editor import ADVANCED_FIELDS, PRIMARY_FIELDS
        assert 'SayTail' in PRIMARY_FIELDS
        assert 'SayTail' not in ADVANCED_FIELDS

    def test_value_survives_with_the_advanced_box_closed(self, qapp):
        from storygraph.ui_qt.card_editor import BeatCardEditor
        ed = BeatCardEditor(symbols=_symbols(dissolve={}))
        ed.load_fields({'kind': 'say', 'text': 't',
                        'SayTail': 'with dissolve'}, [])
        ed._adv_open = False                      # 模拟"高级区收起"
        assert ed.collect_fields().get('SayTail') == 'with dissolve'
