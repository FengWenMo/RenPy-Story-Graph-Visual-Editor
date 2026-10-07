"""
tests/test_choice_picker.py
===========================
**右键 chip 的可搜索候选列表**（用户 2026-10-05）：

> 既然变换如此多，那为什么不加个右键列表筛选跟搜索功能呢？

真实项目的位置候选有 **151 个**（146 个自定义 transform + 内置位置），
靠"点一下换下一个"要按 40 次才轮到 `i11` —— 那不叫能用。

这一层钉两件事：
  · `filter_choices` 是**纯函数**（筛选口径：忽略大小写 + 子串 + 顺序稳定）；
  · 右键那条路与左键那条路**共用 `_set_field`**（值、图像名同步、
    `on_change` 通知三条都一样），不会出现"两条路给出不同名字"。
"""

from __future__ import annotations

import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest  # noqa: E402

from storygraph.ui_qt.choice_picker import filter_choices  # noqa: E402


class TestFilterChoices:
    def test_empty_query_keeps_everything_in_order(self):
        items = ['i11', 'd11', 'center', 'leftish']
        assert filter_choices(items, '') == items
        assert filter_choices(items, '   ') == items

    def test_substring_and_case_insensitive(self):
        items = ['i11', 'd11', 'bi11', 'Center', 'left']
        assert filter_choices(items, 'i1') == ['i11', 'bi11']
        assert filter_choices(items, 'CEN') == ['Center']

    def test_blank_candidates_are_dropped(self):
        assert filter_choices(['a', '', '  ', 'b'], '') == ['a', 'b']

    def test_order_is_stable(self):
        """顺序必须稳定（用户的手指要记得住位置）——不许按相似度重排。"""
        items = ['z9', 'a1', 'm5']
        assert filter_choices(items, '') == items
        assert filter_choices(items, '9') == ['z9']

    def test_no_match_is_empty_not_an_error(self):
        assert filter_choices(['a', 'b'], 'zzz') == []


class TestChipRightClick:
    """右键 chip → 选一个 → 值与图像名一起更新（与左键同一条路）。"""

    @pytest.fixture
    def card(self, qapp):
        from storygraph.assets.asset_registry import AssetRegistry
        from storygraph.story.beat_render import make_symbols
        from storygraph.ui_qt.sprite_grid import SpriteCardWidget

        reg = AssetRegistry()
        reg.images = {'ling_angry': {'ID': 'ling_angry', 'Tag': 'ling',
                                     'Attributes': 'angry'},
                      'ling_happy': {'ID': 'ling_happy', 'Tag': 'ling',
                                     'Attributes': 'happy'}}
        reg.transforms = {'i11': {'ID': 'i11', 'ATL': 'xpos 0.35'},
                          'd11': {'ID': 'd11', 'ATL': 'xpos 0.2'}}
        changed = []
        return SpriteCardWidget(
            {'image': 'ling angry', 'expression': 'angry', 'position': 'd11',
             'at': '', 'speaking': False, 'order_index': 0, 'extra_rows': []},
            symbols=make_symbols(reg), loader=None,
            on_change=lambda _c: changed.append(1), on_resolve_path=None), changed

    def test_picking_a_position_sets_it(self, card, monkeypatch):
        widget, changed = card
        import storygraph.ui_qt.choice_picker as picker
        monkeypatch.setattr(picker, 'choose_from',
                            lambda *a, **k: 'i11')
        widget._pick_from_list('position')
        assert widget.item['position'] == 'i11'
        assert changed, '右键选完必须通知主窗口（否则"改了没保存"）'

    def test_picking_an_expression_syncs_the_image_name(self, card,
                                                        monkeypatch):
        widget, _changed = card
        import storygraph.ui_qt.choice_picker as picker
        monkeypatch.setattr(picker, 'choose_from',
                            lambda *a, **k: 'happy')
        widget._pick_from_list('expression')
        assert widget.item['expression'] == 'happy'
        assert widget.item['image'] == 'ling happy', (
            f'卡片标题（图像名）没跟着改：{widget.item["image"]!r}')

    def test_cancel_changes_nothing(self, card, monkeypatch):
        widget, changed = card
        import storygraph.ui_qt.choice_picker as picker
        monkeypatch.setattr(picker, 'choose_from', lambda *a, **k: '')
        widget._pick_from_list('position')
        assert widget.item['position'] == 'd11'
        assert not changed, '取消不该通知"改了"'

    def test_the_list_is_the_same_source_as_the_left_click(self, card):
        """两条路必须问同一个函数要候选（不可能给出不一样的名字）。"""
        from storygraph.app.beats import choices_for_image
        widget, _changed = card
        opts = choices_for_image(str(widget.item['image']), widget._symbols)
        assert opts['position'] and opts['expression']
        assert 'i11' in opts['position']
