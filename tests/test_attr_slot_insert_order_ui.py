# -*- coding: utf-8 -*-
"""
「清一下这一维、再选回来」顺序会不会歪 —— 界面那条路（2026-11-14）
==================================================================

纯逻辑那半在 `tests/test_attr_slot_insert_order.py`。这里钉的是**真 Qt 控件**：

用户点「✕ 不写这一维」（只是想看看默认长什么样）→ 再点选回来 ——
新词必须回到**它原来的位置**。这正是"我抓到了个不太好的方式"里最自然的那一种：

    lan clothes2 uh → 清服装 → lan uh → 再选 clothes2

**从前**会变成 `lan uh clothes2`（真机 `chapter2_2_3.rpy` 第 17/25/33 行）。
`SpriteCardWidget._set_slot_value` 是 chip 的唯一出口，所以判据直接点它
（PySide6 里槽函数抛异常只打 stderr、界面不动 —— 不真点就测不出这类事故）。
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

pytest.importorskip('PySide6')

from storygraph.assets.registry_load import load_asset_registry      # noqa: E402
from storygraph.story.beat_render import make_symbols                # noqa: E402
from storygraph.story.layered_model import (clear_layer_model_cache,  # noqa: E402
                                            load_layer_model_cached)

DEFINITIONS = '''\
layeredimage lan:
    image_format 'images/characters/lan/{image}.png'
    group outfit:
        attribute aa
        attribute clothes2
    group expressions:
        attribute shy
        attribute uh
'''


@pytest.fixture
def card(qapp, tmp_path):
    """一张 `lan clothes2 uh` 的立绘卡（provider 带项目根与 layeredimage 模型）。"""
    from storygraph.assets.asset_registry import (_TABLE_FIELDS,
                                                  write_dict_to_excel)
    from storygraph.ui_qt.sprite_grid import SpriteCardWidget

    root = tmp_path / 'proj'
    tables = root / 'game' / 'asset_tables'
    tables.mkdir(parents=True, exist_ok=True)
    write_dict_to_excel(
        tables / 'characters.xlsx',
        {'lan': {'ID': 'lan', 'Name': '蓝', 'ImageTag': 'lan'}},
        _TABLE_FIELDS['character'])
    (root / 'game' / 'definitions.rpy').write_text(DEFINITIONS,
                                                   encoding='utf-8')
    # 项目惯例：服装写在表情前面（两条证据）
    (root / 'game' / 'ch01.rpy').write_text(
        'label ch01:\n'
        '    show lan aa shy\n'
        '    "一。"\n'
        '    show lan clothes2 uh\n'
        '    "二。"\n'
        '    return\n', encoding='utf-8')

    clear_layer_model_cache()
    symbols = make_symbols(load_asset_registry(root))
    model = load_layer_model_cached(str(root))

    def groups_for(_tag):
        return []

    groups_for.attr_groups = []
    groups_for.engine_facts = {}
    groups_for.layer_model = model
    groups_for.project_root = str(root)

    item = {'image': 'lan clothes2 uh', 'expression': 'clothes2 uh',
            'position': '', 'at': '', 'speaking': False, 'order_index': 0,
            'extra_rows': []}
    w = SpriteCardWidget(item, symbols=symbols, choice_groups_for=groups_for)
    w._loader = None
    w.refresh()
    return w


def test_清空服装再选回来_回到原来的位置(card):
    assert card.item['image'] == 'lan clothes2 uh'
    card._set_slot_value('outfit', '')          # 「✕ 不写这一维」
    assert card.item['image'] == 'lan uh', card.item['image']
    card._set_slot_value('outfit', 'clothes2')  # 再选回来
    assert card.item['image'] == 'lan clothes2 uh', card.item['image']


def test_原位换一个词_顺序不动(card):
    card._set_slot_value('outfit', 'aa')
    assert card.item['image'] == 'lan aa uh', card.item['image']


def test_清空的是表情_服装留在原位(card):
    card._set_slot_value('expressions', '')
    assert card.item['image'] == 'lan clothes2', card.item['image']
    card._set_slot_value('expressions', 'uh')
    assert card.item['image'] == 'lan clothes2 uh', card.item['image']
