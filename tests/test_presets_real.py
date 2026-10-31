# -*- coding: utf-8 -*-
"""随包的 **两份预设** 是"真项目扫出来的"，不是手抄的骨架。

`storygraph/presets/` 里的两份文件会随发行包发出去、也会被演出专用版补进
项目（`app/stage_edition.py`）。它们要是抄错一个字母，用户在卡片上就选不到
那套衣服 —— 而且**界面不会报错**，只会"下拉里没有"。

所以这里钉四件事：

  ① **工具自己的读取器**能把映射读进来（`load_mapping` 0 警告）——
     拿 `json.loads` 判自己写的文件等于没判；
  ② 代号表的形状：`code` 是单个大写字母或空，`no` 是数字，四列齐全；
  ③ **不猜**：`xing` 的 A/B 在真剧本里只对上一次（证据太弱），代号必须是空的；
     `lan`/`ling`/`ye` 的 A/B 都数到了 2 处以上，代号必须是 A/B；
  ④ 两份文件**互相对得上**：映射里出现的每个服装/表情属性，都能在
     `storygraph.rules.json` 的 `show` 候选里找到（否则卡片下拉里没有它）。

最后用真项目里的两条真事收口：`lan B7` 与 `ye A1` 必须换算成剧本里真写着
的那两行 —— 表错一个字母，这两条立刻红。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.assets import sprite_codes as SC
from storygraph.assets.asset_mapping import load_mapping

ROOT = Path(__file__).resolve().parents[1]
PRESET = ROOT / 'storygraph' / 'presets'
MAPPING = PRESET / 'storygraph.mapping.json'
RULES = PRESET / 'storygraph.rules.json'


@pytest.fixture(scope='module')
def data():
    """随包映射（走工具自己的读取器，警告必须为空）。"""
    got, warnings = load_mapping(str(MAPPING))
    assert not warnings, f'随包映射有警告：{warnings}'
    return got


@pytest.fixture(scope='module')
def rules():
    return json.loads(RULES.read_text(encoding='utf-8'))


def _rows(data, section):
    return list(data.get(section) or ())


def _show_statement(rules):
    return next(s for s in rules['statements'] if s.get('name') == 'show')


# ---------------------------------------------------------------------------
#  ① / ② 形状
# ---------------------------------------------------------------------------

def test_the_preset_mapping_is_readable_by_the_tool(data):
    assert data['schema'] == 'storygraph.mapping/1'
    assert data['characters'], '一个角色都没有 —— 预设没建起来'
    assert _rows(data, SC.SECTION_OUTFITS), '服装代号表是空的'
    assert _rows(data, SC.SECTION_EXPRESSIONS), '表情编号表是空的'


def test_every_outfit_row_is_well_formed(data):
    for row in _rows(data, SC.SECTION_OUTFITS):
        assert set(row) == set(SC.OUTFIT_FIELDS), row
        assert row['char'], row
        assert row['attr'], row
        assert row['code'] == row['code'].upper()
        assert row['code'] == '' or (len(row['code']) == 1
                                    and row['code'] in SC.LETTERS), row


def test_every_expression_row_is_well_formed(data):
    for row in _rows(data, SC.SECTION_EXPRESSIONS):
        assert set(row) == set(SC.EXPRESSION_FIELDS), row
        assert row['char'], row
        assert row['attr'], row
        assert row['no'].isdigit(), row


def test_expression_numbers_are_unique_per_character(data):
    """一号两用会让 `B7` 指向两张脸 —— 表里不许出现。"""
    seen = {}
    for row in _rows(data, SC.SECTION_EXPRESSIONS):
        key = (row['char'], row['no'])
        assert key not in seen, f'{row["char"]} 的 {row["no"]} 号出现了两次'
        seen[key] = row['attr']


# ---------------------------------------------------------------------------
#  ③ 不猜：证据弱的留空，数到的才填
# ---------------------------------------------------------------------------

def test_the_two_outfits_with_enough_evidence_carry_a_letter(data):
    codes = {(r['char'], r['attr']): r['code']
             for r in _rows(data, SC.SECTION_OUTFITS)}
    assert codes[('lan', 'aa')] == 'A'
    assert codes[('lan', 'base_hand_up')] == 'B'
    assert codes[('ling', 'aa')] == 'A'
    assert codes[('ye', 'aa')] == 'A'
    assert codes[('ye', 'base_handdown')] == 'B'


def test_xing_keeps_the_letter_empty(data):
    """真剧本里 xing 的 A/B 各只对上 1 次 —— 留空，不按顺序硬凑。"""
    for row in _rows(data, SC.SECTION_OUTFITS):
        if row['char'] == 'xing':
            assert row['code'] == '', f'xing 的证据不够，却填了 {row["code"]}'


def test_outfits_never_used_by_the_script_keep_an_empty_letter(data):
    """`clothes2` / 泳装 在剧本里一次都没写过 —— 留空等人照美术需求表填。"""
    for row in _rows(data, SC.SECTION_OUTFITS):
        if row['attr'] in ('clothes2', 'base_swimsuit', 'hanfu_base'):
            assert row['code'] == '', row


# ---------------------------------------------------------------------------
#  ④ 两份预设互相对得上
# ---------------------------------------------------------------------------

def test_every_outfit_in_the_mapping_is_a_candidate_in_the_rules(data, rules):
    choices = list(_show_statement(rules)['columns'][1]['choices'])
    for row in _rows(data, SC.SECTION_OUTFITS):
        assert row['attr'] in choices, f'{row["attr"]} 不在「服装」候选里'


def test_every_expression_in_the_mapping_is_a_candidate_in_the_rules(data, rules):
    choices = list(_show_statement(rules)['columns'][2]['choices'])
    for row in _rows(data, SC.SECTION_EXPRESSIONS):
        assert row['attr'] in choices, f'{row["attr"]} 不在「表情」候选里'


def test_the_character_tags_are_candidates(rules):
    """卡片上写 `show lan …`，下拉里就得有 `lan`（不是只有「蓝」）。"""
    choices = list(_show_statement(rules)['columns'][0]['choices'])
    for tag in ('lan', 'ling', 'ye', 'xing', 'qing'):
        assert tag in choices, f'{tag} 不在「角色」候选里'


def test_the_role_cell_is_not_a_closed_enum(rules):
    """`show bgoceanblur` / `show icon` 在真剧本里是合法的"一张图一个字"。"""
    slot = next(s for s in _show_statement(rules)['pattern']
                if s.get('column') == '角色')
    assert slot['limit'] is False


# ---------------------------------------------------------------------------
#  收口：真题换算
# ---------------------------------------------------------------------------

def test_lan_b7_means_the_line_the_script_really_writes(data):
    got = SC.translate('lan B7', data)
    assert got.ok, got.note
    assert got.show == 'show lan base_hand_up indifferent'


def test_ye_a1_means_the_line_the_script_really_writes(data):
    got = SC.translate('ye A1', data)
    assert got.ok, got.note
    assert got.show == 'show ye aa close_happy'


def test_a_letter_we_do_not_have_says_what_is_missing(data):
    got = SC.translate('xing A7', data)
    assert not got.ok
    assert 'A' in got.note
