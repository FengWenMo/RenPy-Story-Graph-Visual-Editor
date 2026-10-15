"""
「找不到那张立绘时，在立绘那里直接显示**没有**」（2026-10-13 用户要求）
================================================================================

> 继续做，不要阻止自定义时填它，但是如果在卡牌编辑器内打开时，
> 若找不到对应的文件，我希望你能给出提示（不必是弹窗，直接在立绘那里显示没有）
> 另外注意保持通用性！不要搞特化

三件事各有一组判据：

  ① **填的时候一个字都不拦** —— 随便写什么都能存进卡片 / 声明
     （这一条是**反向**的：`_load_thumb` 只改显示，绝不改数据）；
  ② **判得了的时候**（项目打开过）找不到 → 立绘格子里写「没有」（**不弹窗**）；
  ③ **判不了的时候**（项目还没打开）**不许**说"没有" —— 那是假警报。

⚠ **通用性**：这里只出现"路径解析不出来"这一个通用规则 ——
没有角色名、没有组名、没有一个字是某个项目的特例。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def make_card(qapp):
    """造一张立绘卡（`resolver` 决定"这张图找不找得到"）。"""
    from storygraph.ui_qt.sprite_grid import SpriteCardWidget

    def _make(spec: str, resolver=None, assets_ready: bool = True):
        card = SpriteCardWidget(
            {'image': spec, 'expression': '', 'position': '', 'at': '',
             'speaking': False, 'order_index': 0, 'extra_rows': []},
            loader=type('L', (), {'project_root': None,
                                  'request': lambda *a, **k: None})(),
            on_resolve_path=resolver or (lambda _s: None),
            assets_ready=assets_ready)
        return card

    return _make


# ===========================================================================
#  ② 判得了 → 找不到就写「没有」
# ===========================================================================

def test_a_missing_sprite_says_nothing_found_right_on_the_card(make_card):
    """
    ⭐⭐ 用户要的那一条：**直接在立绘那里显示"没有"**（不是弹窗、不是只写 tooltip）。
    """
    card = make_card('hanfu_base happy')
    text = card._thumb.text()
    assert '没有' in text, f'立绘格子里没写"没有"：{text!r}'
    # 提示要**说清是哪一张、下一步怎么办**（tooltip 里）
    tip = card._thumb.toolTip()
    assert '找不到' in tip and 'hanfu_base happy' in tip, tip
    assert card._missing is True


def test_an_empty_sprite_slot_still_shows_the_add_button(qapp):
    """空格子照旧是「＋」（那是"加一张"的意思，不是"没有"）。"""
    from storygraph.ui_qt.sprite_grid import SpriteCardWidget
    card = SpriteCardWidget({'image': '', 'expression': '', 'position': '',
                             'at': '', 'speaking': False, 'order_index': 0,
                             'extra_rows': []}, loader=None)
    card._load_thumb('')
    assert card._thumb.text() == '＋', card._thumb.text()
    assert card._missing is False


def test_a_found_sprite_is_not_marked_missing(make_card, tmp_path):
    """找得到 → 不写"没有"，警告色也收回去（`_mark_missing(False)`）。"""
    real = tmp_path / 'ling_oh.png'
    real.write_bytes(b'\x89PNG\r\n\x1a\n')
    card = make_card('ling oh', resolver=lambda _s: str(real))
    assert '没有' not in card._thumb.text(), card._thumb.text()
    assert card._missing is False


# ===========================================================================
#  ③ 判不了 → 不许假警报（通用性的另一半）
# ===========================================================================

def test_before_a_project_is_open_it_does_not_cry_wolf(make_card):
    """
    ⭐⭐ 项目还没打开时**谁也判不了** —— 那时把每张立绘都写成"没有"
    是**假警报**，比不提示更糟（用户会去改根本没问题的卡片）。

    这一条与"通用性"是同一件事：工具不许在信息不足时下结论。
    """
    card = make_card('ling oh', assets_ready=False)
    text = card._thumb.text()
    assert '没有' not in text, f'项目还没打开就说"没有"：{text!r}'
    assert card._missing is False
    assert '还没打开项目' in card._thumb.toolTip(), card._thumb.toolTip()


def test_assets_ready_flips_the_display_both_ways(make_card, tmp_path):
    """
    ⭐ 项目是**后**打开的（卡片先画出来）→ 说一声之后提示要跟着出来；
    反过来（判不了了）提示要**收回去** —— 两个方向都钉住，
    否则会出现"好图一直顶着警告色"这种脏状态。
    """
    card = make_card('ling oh', assets_ready=False)
    assert '没有' not in card._thumb.text()
    card.set_assets_ready(True)
    assert '没有' in card._thumb.text(), card._thumb.text()
    # 现在让这张图"变得找得到" → 收回警告
    real = tmp_path / 'ling_oh.png'
    real.write_bytes(b'\x89PNG\r\n\x1a\n')
    card._on_resolve_path = lambda _s: str(real)
    card.set_assets_ready(False)
    card.set_assets_ready(True)
    assert card._missing is False, '好图还挂着"没有"'
    assert '没有' not in card._thumb.text(), card._thumb.text()


# ===========================================================================
#  ① 反向判据：只改显示，绝不动数据
# ===========================================================================

def test_showing_missing_never_touches_the_card_data(make_card):
    """
    ⭐⭐ 用户："**不要阻止**自定义时填它" —— 这条是**反向**判据：

    提示只是**画出来**的，卡片里的 `image` / `expression` 一个字都不能被改掉，
    更不能把这张卡删了或清空（那样用户填的东西就没了）。
    """
    card = make_card('hanfu_base happy')
    assert card.item['image'] == 'hanfu_base happy', card.item
    assert card.expression() if hasattr(card, 'expression') else True
    # 再画几次也不许改数据
    for _ in range(3):
        card._load_thumb(str(card.item['image']))
    assert card.item['image'] == 'hanfu_base happy', card.item


def test_the_missing_state_is_visible_not_only_in_the_tooltip(make_card):
    """
    ⚠ 只写 tooltip 等于**没提示**（用户不会去悬停）。

    判据：格子上的**文字**必须变（以前"找不到"时只是把 tag 名显示出来，
    与"还没解析"长得一模一样，所以用户根本看不出区别）。
    """
    card = make_card('hanfu_base happy')
    assert card._thumb.text() != 'hanfu_base', \
        '只显示了 tag 名 —— 与"还没解析"分不出来，等于没提示'
