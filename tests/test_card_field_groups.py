# -*- coding: utf-8 -*-
"""
卡片右栏的**字段组**必须跟"这一拍是什么"对应（2026-11-07 用户要求）
======================================================================

用户原话：

> 卡片编辑器右边的 UI 和自定义模块定义出的模块一定要对应！UI 要重构……

具体要防的是这张卡：一条 `泳装切换 小明 泳装 开心` 的自定义语句，右栏却
铺着"说话人 / 台词 / 背景 / 立绘"四个空块 —— 跟这条语句毫无关系，还把真正
的三格参数挤到角落里。

两条规则（都按"有没有值 / 是不是语句拍"判，不按 `kind` 猜）：

  1. 没有台词 → 收起台词区；
  2. **语句拍**且没有舞台动作（场景/立绘/退场/舞台高级）→ 收起舞台块。

⚠ 最要命的一条在最后：**收起不等于不写回**。高级区那边是"展开才收"，
`SayTail` 曾因此静默丢过一次；这里的收起必须只是 `setVisible(False)`。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

STATEMENT = {'name': '泳装切换', 'label': '泳装切换', 'head': '泳装切换'}


def _card(qapp):
    from storygraph.ui_qt.card_editor import BeatCardEditor
    return BeatCardEditor()


def _load(card, fields, statement=None):
    """载入一拍，并按这一拍的语句重算参数组（与窗口那条路同序）。"""
    card.load_fields(dict(fields), [])
    card.set_spec_args([], {}, {}, statement=statement)


def _close(card):
    try:
        card.shutdown_stage_thumb()
    except AttributeError:
        pass


def test_a_statement_card_hides_the_dialogue_and_stage_blocks(qapp):
    card = _card(qapp)
    try:
        _load(card, {'Text': '', 'Show': '', 'Scene': ''}, STATEMENT)
        assert card._dialogue_box.isHidden(), '自定义语句拍不该铺着台词框'
        assert card._stage_box.isHidden(), '自定义语句拍不该铺着背景/立绘输入'
    finally:
        _close(card)


def test_a_plain_say_card_keeps_both_blocks(qapp):
    card = _card(qapp)
    try:
        _load(card, {'Text': '一句台词'}, None)
        assert not card._dialogue_box.isHidden()
        assert not card._stage_box.isHidden(), '普通台词拍要保持原样（能顺手 show）'
    finally:
        _close(card)


def test_a_show_card_hides_only_the_dialogue(qapp):
    card = _card(qapp)
    try:
        _load(card, {'Text': '', 'Show': 'lan happy'}, None)
        assert card._dialogue_box.isHidden(), '没有台词就不该有台词框'
        assert not card._stage_box.isHidden(), '这一拍在改立绘，舞台块必须显示'
    finally:
        _close(card)


def test_a_statement_card_with_a_line_keeps_the_dialogue(qapp):
    """自定义语句顺带一句台词是合法的（`mycue "闪电"` 后面接台词）。"""
    card = _card(qapp)
    try:
        _load(card, {'Text': '顺带一句'}, STATEMENT)
        assert not card._dialogue_box.isHidden(), '有台词就得让人看见台词'
        assert card._stage_box.isHidden(), '语句拍没动舞台，那块仍是噪音'
    finally:
        _close(card)


def test_a_statement_card_with_a_show_keeps_the_stage_block(qapp):
    card = _card(qapp)
    try:
        _load(card, {'Text': '', 'Show': 'lan happy'}, STATEMENT)
        assert card._stage_box.isHidden() is False
    finally:
        _close(card)


# ----------------------------------------------------------------------
#  ⚠ 收起 ≠ 不写回（这条比上面几条都重要）
# ----------------------------------------------------------------------

def test_hidden_groups_are_still_collected(qapp):
    """
    收起只是 `setVisible(False)`；`collect_fields()` 照旧把这些字段收出来。

    反例就是高级区那套"展开才收"—— `SayTail` 曾经因此**静默丢**过一次
    （用户把台词转场写在收起的高级区里，保存时没了）。
    """
    card = _card(qapp)
    try:
        _load(card, {'Text': ''}, STATEMENT)
        assert card._dialogue_box.isHidden()
        # 收起之后**仍然**能从控件读出来（程序化改值也算 —— 说明控件还在、
        # 还在 `_widgets` 里，只是不可见）
        card._widgets['Text'].setPlainText('程序写进来的台词')
        assert card.collect_fields().get('Text') == '程序写进来的台词'
    finally:
        _close(card)


def test_switching_back_to_a_say_card_restores_the_blocks(qapp):
    """翻页：语句拍 → 台词拍，收起的组必须放回来（不能一直收着）。"""
    card = _card(qapp)
    try:
        _load(card, {'Text': ''}, STATEMENT)
        assert card._dialogue_box.isHidden()
        _load(card, {'Text': '下一句'}, None)
        assert not card._dialogue_box.isHidden()
        assert not card._stage_box.isHidden()
    finally:
        _close(card)


# ----------------------------------------------------------------------
#  高级区：默认展开 + **收起也写回**（Unity 的 Foldout 语义）
# ----------------------------------------------------------------------

def test_the_advanced_area_starts_expanded(qapp):
    """
    Unity 的 `Foldout` 默认是开的：折叠是"这屏看完了"的临时动作，
    不是"藏起来不用管"。默认收起会让人以为"这块没东西"。
    """
    card = _card(qapp)
    try:
        assert card._adv_host.isHidden() is False
        assert card._adv_open is True
    finally:
        _close(card)


def test_collapsing_the_advanced_area_still_writes_back(qapp):
    """
    ⚠ 收起只影响**显示**，不改变"要不要保存"。

    从前 `collect_fields` 里是 `if self._adv_open:` —— 收了高级区，
    里面的字段就**不算数**了。`SayTail` 曾经因此静默丢过一次
    （当时靠把 SayTail 挪到主区绕过去，根因没修）。这条钉住根因。
    """
    card = _card(qapp)
    try:
        _load(card, {'Text': '一句台词'}, None)
        card._adv_open = False
        card._adv_host.setVisible(False)          # 模拟人点了收起
        card._widgets['Label'].setText('ch01')
        out = card.collect_fields()
        assert out.get('Label') == 'ch01', '收起高级区之后字段就不写回了（会静默丢）'
    finally:
        _close(card)


# ----------------------------------------------------------------------
#  高级区：默认展开 + **收起也写回**（Unity 的 Foldout 语义）
# ----------------------------------------------------------------------

def test_the_advanced_area_starts_expanded(qapp):
    """
    Unity 的 `Foldout` 默认是开的：折叠是"这屏看完了"的临时动作，
    不是"藏起来不用管"。默认收起会让人以为"这块没东西"。
    """
    card = _card(qapp)
    try:
        assert card._adv_host.isHidden() is False
        assert card._adv_open is True
    finally:
        _close(card)


def test_collapsing_the_advanced_area_still_writes_back(qapp):
    """
    ⚠ 收起只影响**显示**，不改变"要不要保存"。

    从前 `collect_fields` 里是 `if self._adv_open:` —— 收了高级区，
    里面的字段就**不算数**了。`SayTail` 曾经因此静默丢过一次
    （当时靠把 SayTail 挪到主区绕过去，根因没修）。这条钉住根因。
    """
    card = _card(qapp)
    try:
        _load(card, {'Text': '一句台词'}, None)
        card._adv_open = False
        card._adv_host.setVisible(False)          # 模拟人点了收起
        card._widgets['Label'].setText('ch01')
        out = card.collect_fields()
        assert out.get('Label') == 'ch01', '收起高级区之后字段就不写回了（会静默丢）'
    finally:
        _close(card)
