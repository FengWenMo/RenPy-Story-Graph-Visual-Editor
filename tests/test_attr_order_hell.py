# -*- coding: utf-8 -*-
"""
属性顺序地狱测试（纯逻辑那一半）—— 2026-11-14
==============================================

用户："写一个地狱测试，保证它不再发生……"

"它" = **同一个项目里立绘属性被写成两种顺序**：全项目 1269 条
`show lan aa shy`，却混进 3 条 `show lan shy aa`，作者并没有那样写
（真机 `chapter2_2_3.rpy` 第 17/25/33 行）。

这一份钉两条**不依赖界面**的不变量：

  I1 **点选顺序无关**：项目自己写过 ≥2 条"服装在前"的行 → 无论先点服装
     还是先点表情，落盘的写法必须一样（都等于项目惯例）。
  I2 **只动这一维**：换 / 加 / 删一个维度时，其余词逐词原位；`@位置` 跟着
     它的词；子句（`at` / `with` / `behind`）一个字节都不动；不留孤儿 `@`。

另加一组"地狱输入"：脏值、拼法变体、未知词、自定义子句关键字、三维度、
连清再填 —— 只要求两件事：**不崩**、**不悄悄改别的词**。

界面那一半（清空再选回来、连点几十下、右键组合）在
`tests/test_attr_order_hell_ui.py`。

⚠ 与既有判据的分工：
  · `test_attr_slot_order.py`：换一维**不许改动其余维度的顺序**（2026-11-05）；
  · `test_attr_slot_insert_order.py`：这一维**原来没写**时插在哪（2026-11-14）；
  · 这一份：**任意顺序、任意次数**操作之后，写法仍然只有一种。
"""
from __future__ import annotations

import itertools
import random

import pytest


# ---------------------------------------------------------------------------
#  一个"三维度"的项目：服装 → 表情 → 动作（作者两行都是这么写的）
# ---------------------------------------------------------------------------

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

CHAPTER = ('label ch01:\n'
           '    show lan aa shy stand\n'
           '    "一。"\n'
           '    show lan clothes2 oh sit\n'
           '    "二。"\n'
           '    show lan aa uh\n'
           '    "三。"\n'
           '    return\n')

#: 只有一条证据 → 学不出项目惯例（`attr_order.MIN_EVIDENCE = 2`）。
NO_CONVENTION = 'label ch01:\n    show lan aa shy\n    "一。"\n    return\n'

#: 三维度的每一维各取一个值（顺序 = 项目惯例）。
DIMS = [('outfit', 'aa'), ('expressions', 'oh'), ('pose', 'stand')]


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


def _env(base, chapter):
    from storygraph.assets.registry_load import load_asset_registry
    from storygraph.story.beat_render import make_symbols
    from storygraph.story.layered_model import (clear_layer_model_cache,
                                                load_layer_model_cached)

    root = _project(base, chapter)
    clear_layer_model_cache()
    symbols = make_symbols(load_asset_registry(root))
    model = load_layer_model_cached(str(root))
    return root, symbols, model


@pytest.fixture(scope='module')
def env(tmp_path_factory):
    """有惯例的项目（服装 → 表情 → 动作）。"""
    return _env(tmp_path_factory.mktemp('conv'), CHAPTER)


@pytest.fixture(scope='module')
def env_no_conv(tmp_path_factory):
    """证据不足的项目（学不出惯例）。"""
    return _env(tmp_path_factory.mktemp('noconv'), NO_CONVENTION)


def _apply(env, spec, group, value, **kw):
    from storygraph.app.beats import apply_attr_slot

    _root, symbols, model = env
    return apply_attr_slot(spec, group, value, symbols=symbols,
                           layer_model=model, **kw)


def _split(spec):
    from storygraph.app.beats import split_show_spec

    return split_show_spec(spec)


def _bases(words):
    return [str(w).split('@', 1)[0] for w in words]


def _slot_value(env, spec, group):
    from storygraph.app.beats import attr_slot_rows

    _root, symbols, model = env
    rows = attr_slot_rows(spec, symbols, layer_model=model)
    hit = next((r for r in rows if str(r.get('group') or '') == group), None)
    return str((hit or {}).get('value') or '')


# ---------------------------------------------------------------------------
#  I1 点选顺序无关
# ---------------------------------------------------------------------------

class TestClickOrderIsIrrelevant:

    def test_every_permutation_from_a_bare_tag_lands_on_the_same_line(self, env):
        """三个维度、六种点选顺序 → **逐字节同一条**。"""
        got = set()
        for perm in itertools.permutations(DIMS):
            spec = 'lan'
            for group, value in perm:
                spec = _apply(env, spec, group, value)
            got.add(spec)
        assert got == {'lan aa oh stand'}, got

    def test_adding_the_missing_dimensions_in_every_order(self, env):
        """已经有一维（`lan aa`）→ 补另外两维，两种先后都得到 `lan aa oh stand`。"""
        got = set()
        for perm in itertools.permutations(DIMS[1:]):
            spec = 'lan aa'
            for group, value in perm:
                spec = _apply(env, spec, group, value)
            got.add(spec)
        assert got == {'lan aa oh stand'}, got

    def test_the_users_exact_scenario(self, env):
        """用户报的那条：`show lan oh` 点「服装」→ 必须是 `lan aa oh`。"""
        direct = _apply(env, 'lan oh', 'outfit', 'aa')
        after_outfit = _apply(env, 'lan', 'outfit', 'aa')
        via_two_steps = _apply(env, after_outfit, 'expressions', 'oh')
        assert direct == via_two_steps == 'lan aa oh'

    def test_card_order_beats_project_convention_for_this_card(self, env):
        """
        这张卡作者**写反了**（`lan shy aa`）→ 清掉服装再选回来，还是写反的。

        用户不接受的是"工具把我的顺序改了"，不是"作者本人写反"。
        所以"插回原位"必须优先于"按项目惯例重排"。
        """
        got = _apply(env, 'lan shy', 'outfit', 'aa', order=['expressions', 'outfit'])
        assert got == 'lan shy aa', got

    def test_insert_at_still_wins_over_everything(self, env):
        assert _apply(env, 'lan shy', 'outfit', 'aa', insert_at=1,
                      order=['expressions', 'outfit']) == 'lan shy aa'


# ---------------------------------------------------------------------------
#  I2 只动这一维
# ---------------------------------------------------------------------------

SHAPES = [
    'lan aa shy',
    'lan shy aa',
    'lan aa shy stand',
    'lan aa@l11 shy',
    'lan aa shy@l22',
    'lan aa shy at i11',
    'lan aa shy at i11 with dissolve',
    'lan zzz_unknown aa shy',
    'lan _hd_cut aa shy behind ye',
]


def _assert_single_slot_change(env, spec, group, value):
    """换/加/删一维之后：tag、子句、其余词的原位顺序都不许动。"""
    before_tag, before_attrs, before_clauses = _split(spec)
    got = _apply(env, spec, group, value)
    after_tag, after_attrs, after_clauses = _split(got)
    assert after_tag == before_tag, f'{spec} → {got}'
    assert after_clauses == before_clauses, f'{spec} → {got}'
    old = _slot_value(env, spec, group)
    before = _bases(before_attrs)
    after = _bases(after_attrs)
    # 去掉"被换掉的那一个词"之后，剩下的词**逐词同序**
    expect = list(before)
    if old and old in expect:
        expect.remove(old)
    kept = list(after)
    if value and value in kept:
        kept.remove(value)
    assert kept == expect, f'{spec} → {got}'
    # 不留孤儿 `@`、不重词
    assert not [a for a in after_attrs if a.startswith('@')], got
    assert len(after) == len(set(after)), got
    return got


class TestOnlyThatDimensionMoves:

    @pytest.mark.parametrize('spec', SHAPES)
    @pytest.mark.parametrize('group', ['outfit', 'expressions', 'pose'])
    def test_replacing_a_word_keeps_everything_else(self, env, spec, group):
        if not _slot_value(env, spec, group):
            pytest.skip('这一维不在这条 spec 里')
        _assert_single_slot_change(env, spec, group, 'zzz_new')

    @pytest.mark.parametrize('spec', SHAPES)
    @pytest.mark.parametrize('group', ['outfit', 'expressions', 'pose'])
    def test_clearing_a_word_keeps_everything_else(self, env, spec, group):
        if not _slot_value(env, spec, group):
            pytest.skip('这一维不在这条 spec 里')
        got = _assert_single_slot_change(env, spec, group, '')
        assert 'zzz_new' not in got

    @pytest.mark.parametrize('spec', SHAPES)
    def test_unknown_group_is_untouched(self, env, spec):
        assert _apply(env, spec, '没这个组', 'aa') == spec
        assert _apply(env, spec, '', 'aa') == spec


class TestClearKeepsInlinePosition:
    """`@位置` 是**这张图**的位置：删词不删位置，也不许留下孤儿 `@`。"""

    @pytest.mark.parametrize('spec,group,expected', [
        ('lan aa@l11 shy', 'outfit', 'lan shy@l11'),
        ('lan aa shy@l22', 'expressions', 'lan aa@l22'),
        ('lan aa@l11 shy at i11', 'outfit', 'lan shy@l11 at i11'),
        ('lan aa shy@l22 stand', 'expressions', 'lan aa stand@l22'),
    ])
    def test_position_moves_to_the_last_remaining_word(self, env, spec, group,
                                                       expected):
        got = _apply(env, spec, group, '')
        assert got == expected, got
        assert not [w for w in got.split() if w.startswith('@')], got

    def test_clearing_the_only_word_never_leaves_a_bare_at(self, env):
        """最后一个属性词被删掉 → 宁可丢位置，也不许留下 `lan @l11`。"""
        got = _apply(env, 'lan shy@l11', 'expressions', '')
        assert got == 'lan', got


# ---------------------------------------------------------------------------
#  没有惯例的项目：行为必须与历史一致（追加），绝不瞎插
# ---------------------------------------------------------------------------

class TestNoConventionNeverGuesses:

    def test_adds_a_missing_dimension_at_the_end(self, env_no_conv):
        assert _apply(env_no_conv, 'lan shy', 'outfit', 'aa') == 'lan shy aa'

    def test_replacement_still_keeps_the_position(self, env_no_conv):
        assert _apply(env_no_conv, 'lan shy aa', 'outfit',
                      'clothes2') == 'lan shy clothes2'

    def test_card_order_still_applies_when_given(self, env_no_conv):
        got = _apply(env_no_conv, 'lan shy', 'outfit', 'aa',
                     order=['outfit', 'expressions'])
        assert got == 'lan aa shy', got


# ---------------------------------------------------------------------------
#  III 地狱输入：随机操作 200 次，只许不崩、不许改坏别的词
# ---------------------------------------------------------------------------

class TestHellInputs:

    VALUES = ['aa', 'clothes2', 'shy', 'oh', 'uh', 'stand', '',
              'zzz_new', 'aa@l33']

    def test_random_operations_never_corrupt_a_line(self, env):
        rng = random.Random(20261114)
        groups = ['outfit', 'expressions', 'pose', '没这个组', '']
        for i in range(200):
            spec = rng.choice(SHAPES)
            group = rng.choice(groups)
            value = rng.choice(self.VALUES)
            before_tag, before_attrs, before_clauses = _split(spec)
            got = _apply(env, spec, group, value)
            where = f'#{i} {spec!r} + {group!r}={value!r} → {got!r}'
            assert got, where
            after_tag, after_attrs, after_clauses = _split(got)
            assert after_tag == before_tag, where
            assert after_clauses == before_clauses, where
            bases = _bases(after_attrs)
            assert len(bases) == len(set(bases)), where
            assert not [a for a in after_attrs if a.startswith('@')], where
            if group in ('没这个组', ''):
                assert got == spec, where

    @pytest.mark.parametrize('spec', [
        '', '   ', 'lan', 'lan   ', '\tlan aa',
        'lan aa  shy', 'lan 中文 aa', 'lan:', 'lan aa;', 'show',
    ])
    def test_dirty_specs_do_not_crash(self, env, spec):
        got = _apply(env, spec, 'outfit', 'aa')
        assert isinstance(got, str)

    def test_same_value_twice_is_a_no_op(self, env):
        once = _apply(env, 'lan shy aa', 'outfit', 'clothes2')
        twice = _apply(env, once, 'outfit', 'clothes2')
        assert once == twice == 'lan shy clothes2'

    def test_clearing_twice_keeps_the_position_of_the_missing_word(self, env):
        """连清两次（第二次已经没词了）→ 再选回来仍在原位。"""
        spec = 'lan shy aa'
        _apply(env, spec, 'outfit', '')
        _apply(env, 'lan shy', 'outfit', '')
        got = _apply(env, 'lan shy', 'outfit', 'aa',
                     order=['expressions', 'outfit'])
        assert got == 'lan shy aa', got
