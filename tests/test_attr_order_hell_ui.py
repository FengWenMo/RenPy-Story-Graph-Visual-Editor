# -*- coding: utf-8 -*-
"""
属性顺序地狱测试（界面那一半）—— 2026-11-14
============================================

纯逻辑那一半在 `tests/test_attr_order_hell.py`。这一份盯**用户真实的那串动作**：

    清空一维 → 再选回来 ／ 连点几十下 ／ 连清两维再依次填回来 ／ 右键组合

判据只有一条，但比"某几个例子对了"强得多：

    **从任意状态出发，把每一维清空、再按任意顺序填回原值，
     落盘字节必须与动手之前完全一致。**

为什么这条判据能挡住这次的事故：2026-11-14 那 3 行 `show lan shy aa`
就是"这一维原来没有词 → 新词被追加到末尾"。只要"清空再选回来"能把每一维
放回原位，追加就再无立足之地 —— 而且这条判据不管点的是第几维、项目有没有
惯例、作者是不是自己写反了。

另外每一步都要满足：只动这一维、不重词、不留孤儿 `@`、子句不动、
`expression` 与 `image` 同步（否则"看到的"和"写下去的"又不是一回事）。

⚠ 这条判据要求的是 `SpriteCardWidget`（真 Qt 控件）——PySide6 只在开发机装，
CI 上没有时整份 skip（与既有 UI 判据同一条规矩）。
"""
from __future__ import annotations

import itertools
import os
import random

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
        attribute oh
        attribute uh
    group pose:
        attribute stand
        attribute sit
'''

#: 作者自己写的：服装 → 表情 → 动作（两行证据）
CONVENTION = ('label ch01:\n'
              '    show lan aa shy stand\n'
              '    "一。"\n'
              '    show lan clothes2 oh sit\n'
              '    "二。"\n'
              '    return\n')

#: 只有一条证据 → 学不出项目惯例
NO_CONVENTION = 'label ch01:\n    show lan aa shy\n    "一。"\n    return\n'


def _project(base, chapter):
    from storygraph.assets.asset_registry import (_TABLE_FIELDS,
                                                  write_dict_to_excel)

    root = base / 'proj'
    tables = root / 'game' / 'asset_tables'
    tables.mkdir(parents=True, exist_ok=True)
    write_dict_to_excel(
        tables / 'characters.xlsx',
        {'lan': {'ID': 'lan', 'Name': '蓝', 'ImageTag': 'lan'}},
        _TABLE_FIELDS['character'])
    (root / 'game' / 'definitions.rpy').write_text(DEFINITIONS,
                                                   encoding='utf-8')
    (root / 'game' / 'ch01.rpy').write_text(chapter, encoding='utf-8')
    return root


@pytest.fixture(scope='module')
def projects(tmp_path_factory):
    """两套项目：有惯例 / 没有惯例。"""
    out = {}
    for kind, chapter in (('conv', CONVENTION), ('noconv', NO_CONVENTION)):
        root = _project(tmp_path_factory.mktemp(kind), chapter)
        clear_layer_model_cache()
        out[kind] = (root, make_symbols(load_asset_registry(root)),
                     load_layer_model_cached(str(root)))
    return out


@pytest.fixture
def card(qapp, projects):
    """造一张卡（真 symbols + 真 layeredimage 模型，provider 带项目根）。"""
    from storygraph.ui_qt.sprite_grid import SpriteCardWidget

    def _make(kind: str, spec: str):
        root, symbols, model = projects[kind]

        def groups_for(_tag):
            return []

        groups_for.attr_groups = []
        groups_for.engine_facts = {}
        groups_for.layer_model = model
        groups_for.project_root = str(root)
        item = {'image': spec,
                'expression': ' '.join(spec.split()[1:]),
                'position': '', 'at': '', 'speaking': False,
                'order_index': 0, 'extra_rows': []}
        w = SpriteCardWidget(item, symbols=symbols,
                             choice_groups_for=groups_for)
        w._loader = None                      # 不真去读缩略图
        w.refresh()
        return w

    return _make


# ---------------------------------------------------------------------------
#  小工具
# ---------------------------------------------------------------------------

def _value_of(w, group: str) -> str:
    hit = next((r for r in w._slot_rows()
                if str(r.get('group') or '') == group), None)
    return str((hit or {}).get('value') or '')


def _groups(w) -> list:
    return [str(r.get('group') or '') for r in w._slot_rows()]


def _options(w) -> dict:
    return {str(r.get('group') or ''): list(r.get('options') or [])
            for r in w._slot_rows()}


def _attrs(w) -> list:
    return [a.split('@', 1)[0] for a in str(w.item['image']).split()[1:]]


def _assert_sane(w, original_spec: str) -> None:
    """
    任何一步之后都不许出现的四种坏样子。

    ⚠ 只给**没有子句**的 spec 用（`expression` 列存的是"tag 之后整串"，
    带 `at …` 时它包含子句，而 `split_show_spec` 的 attrs 已把子句切走）。
    """
    from storygraph.app.beats import split_show_spec

    spec = str(w.item['image'])
    tag0, _a0, clauses0 = split_show_spec(original_spec)
    tag, attrs, clauses = split_show_spec(spec)
    where = f'{original_spec!r} → {spec!r}'
    assert tag == tag0, where
    assert clauses == clauses0, where
    bases = [a.split('@', 1)[0] for a in attrs]
    assert len(bases) == len(set(bases)), f'重词了：{where}'
    assert not [a for a in attrs if a.startswith('@')], f'孤儿 @：{where}'
    assert all(a.count('@') <= 1 for a in attrs), f'双 @：{where}'
    assert str(w.item.get('expression') or '') == ' '.join(attrs), \
        f'expression 和 image 不同步：{where}'


# ---------------------------------------------------------------------------
#  一、清空一维再选回来 = 逐字节不变
# ---------------------------------------------------------------------------

CASES = [
    # (项目, 作者怎么写的那一行, 要清的维度)
    ('conv', 'lan aa shy', ['outfit', 'expressions']),
    ('conv', 'lan shy aa', ['outfit', 'expressions']),
    ('conv', 'lan uh aa', ['expressions', 'outfit']),
    ('conv', 'lan aa shy stand', ['outfit', 'expressions', 'pose']),
    ('conv', 'lan shy at i11 with dissolve', ['expressions']),
    ('noconv', 'lan aa shy', ['outfit', 'expressions']),
    ('noconv', 'lan shy aa', ['outfit', 'expressions']),
]


@pytest.mark.parametrize('kind,spec,groups', CASES)
def test_clear_then_refill_is_byte_identical(card, kind, spec, groups):
    w = card(kind, spec)
    for group in groups:
        value = _value_of(w, group)
        assert value, f'{spec!r} 里没有 {group}'
        w._set_slot_value(group, '')                 # 「✕ 不写这一维」
        assert value not in _attrs(w), w.item['image']
        w._set_slot_value(group, value)              # 再选回来
        assert w.item['image'] == spec, w.item['image']


def test_clearing_every_dimension_and_filling_back_in_any_order(card):
    """
    连清两维／三维再依次填回来 —— 顺序必须还是**作者那一行的顺序**。

    这正是"记下标"办不到的：`lan aa oh stand` 清完服饰和表情，旧下标全失效。
    """
    spec = 'lan aa oh stand'
    values = {'outfit': 'aa', 'expressions': 'oh', 'pose': 'stand'}
    w = card('conv', spec)
    for order in itertools.permutations(['outfit', 'expressions', 'pose']):
        for group in values:
            w._set_slot_value(group, '')
        for group in order:
            w._set_slot_value(group, values[group])
        assert w.item['image'] == spec, f'{order} → {w.item["image"]}'


def test_authors_own_reversed_line_keeps_its_order(card):
    """作者手写的 `lan shy aa` 清一下再选回来，**仍然是他写的那个顺序**。"""
    w = card('conv', 'lan shy aa')
    w._set_slot_value('outfit', '')
    assert w.item['image'] == 'lan shy'
    w._set_slot_value('outfit', 'aa')
    assert w.item['image'] == 'lan shy aa', w.item['image']


def test_clearing_a_word_with_inline_position_keeps_the_position(card):
    """`aa@l11` 被清掉 → 位置挪到剩下的词上，不许变成 `lan @l11 shy`。"""
    w = card('conv', 'lan aa@l11 shy')
    w._set_slot_value('outfit', '')
    assert w.item['image'] == 'lan shy@l11', w.item['image']
    assert w.item['expression'] == 'shy@l11', w.item['expression']


# ---------------------------------------------------------------------------
#  二、连点（左键循环）：空也在圈里，转一圈回来位置不变
# ---------------------------------------------------------------------------

def test_cycling_through_every_value_and_back_keeps_the_slot(card):
    """
    `lan uh aa`（服装在**第二位**）连点服装：aa → clothes2 → 空 → aa。

    中间经过"空"的时候最容易把它挪到第一位 —— 那正是用户报的症状。
    """
    w = card('conv', 'lan uh aa')
    seen = []
    for _ in range(3):                              # aa → clothes2 → 空 → aa
        w._cycle_slot('outfit')
        seen.append(w.item['image'])
    assert seen == ['lan uh clothes2', 'lan uh', 'lan uh aa'], seen
    assert w.item['image'] == 'lan uh aa', w.item['image']


def test_cycling_never_changes_the_other_dimension(card):
    w = card('conv', 'lan shy stand')
    before = _value_of(w, 'pose')
    for _ in range(5):
        w._cycle_slot('outfit')
        _assert_sane(w, 'lan shy stand')
        assert _value_of(w, 'pose') == before


# ---------------------------------------------------------------------------
#  三、右键组合那条路（`slot_choices` 的「✕ 不写这一维」）
# ---------------------------------------------------------------------------

def test_combo_menu_clear_then_pick_the_same_combination_is_identity(card):
    """
    右键组合里"✕ 不写「服装」" → 再选回同一条 —— 与 chip 那条路同一个结果。

    这条路**不经过** `_set_slot_value`（它直接拿整串 spec 落盘），从前是第二个
    入口：记忆放到 `_slot_apply`（`order=`）/`card_order` 之后两处才一致。
    """
    from storygraph.ui_qt.sprite_codes_view import slot_choices

    spec = 'lan uh aa'
    w = card('conv', spec)

    def _choices(current):
        rows = w._slot_rows(current)
        return slot_choices(current, 'lan', rows, w._vocabulary(),
                            lambda g, v: w._slot_apply(current, g, v))

    cleared = next(v[0] for grp in _choices(spec) if grp['group'] == 'outfit'
                   for v in grp['values'] if v[1].startswith('✕'))
    assert cleared == 'lan uh', cleared
    w._apply_image_spec(cleared)
    assert w.item['image'] == 'lan uh'

    full = next(v[0] for grp in _choices(w.item['image'])
                if grp['group'] == 'outfit'
                for v in grp['values'] if v[0] == 'lan uh aa')
    assert full == 'lan uh aa', full
    w._apply_image_spec(full)
    assert w.item['image'] == 'lan uh aa'


# ---------------------------------------------------------------------------
#  四、随机游走：每一步都不许改坏，最后全部清空再填回 = 原样
# ---------------------------------------------------------------------------

WALKS = [
    ('conv', 'lan aa shy', 0),
    ('conv', 'lan aa shy', 1),
    ('conv', 'lan shy aa', 2),
    ('conv', 'lan aa oh stand', 3),
    ('conv', 'lan shy stand', 4),
    ('noconv', 'lan shy aa', 5),
    ('noconv', 'lan aa shy', 6),
]


def _walk(w, seed: int, steps: int) -> None:
    rng = random.Random(seed)
    groups = _groups(w)
    opts = _options(w)
    for _ in range(steps):
        op = rng.choice(['set', 'cycle', 'combo'])
        group = rng.choice(groups)
        if op == 'set':
            w._set_slot_value(group, rng.choice(opts[group] + ['']))
        elif op == 'cycle':
            w._cycle_slot(group)
        else:
            # 右键组合：按**这张卡自己的顺序**挑一组值（可能少挑几维）
            order = [g for g in (w._slot_order or groups) if g in opts]
            picked = [(g, rng.choice(opts[g])) for g in order
                      if rng.random() < 0.7]
            if not picked:
                picked = [(order[0], rng.choice(opts[order[0]]))]
            w._apply_image_spec(' '.join(['lan'] + [v for _g, v in picked]))


@pytest.mark.parametrize('kind,spec,seed', WALKS)
def test_random_walk_never_corrupts_then_restores_exactly(card, kind, spec,
                                                          seed):
    w = card(kind, spec)
    initial = {g: _value_of(w, g) for g in _groups(w)}
    _walk(w, seed, steps=10)
    _assert_sane(w, spec)

    # 全部清空 → 按随机顺序填回**最初的值** → 必须与动手之前逐字节相同
    rng = random.Random(seed * 100 + 7)
    for group in rng.sample(list(initial), len(initial)):
        w._set_slot_value(group, '')
        _assert_sane(w, spec)
    for group in rng.sample(list(initial), len(initial)):
        if initial[group]:
            w._set_slot_value(group, initial[group])
        _assert_sane(w, spec)
    assert w.item['image'] == spec, w.item['image']


# ---------------------------------------------------------------------------
#  五、导出：卡片上看到的顺序 = 写进脚本的顺序
# ---------------------------------------------------------------------------

def test_export_writes_the_order_the_card_shows(card):
    from storygraph.app.beats import cards_to_show, split_show_items

    w = card('conv', 'lan uh aa')
    w._set_slot_value('outfit', 'clothes2')          # 原位换词
    line = cards_to_show([w.item], w._symbols)
    items = split_show_items(line)
    assert items and items[0]['image'] == w.item['image'], line
    assert items[0]['image'] == 'lan uh clothes2', line


def test_export_after_clear_and_refill_is_the_same_line(card):
    from storygraph.app.beats import cards_to_show

    w = card('conv', 'lan shy aa')
    before = cards_to_show([w.item], w._symbols)
    w._set_slot_value('outfit', '')
    w._set_slot_value('outfit', 'aa')
    assert cards_to_show([w.item], w._symbols) == before
