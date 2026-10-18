# -*- coding: utf-8 -*-
"""
第五轮修完之后的**护栏**：写回的区间来源、`@N` 的语义、`required` 的来源
================================================================================

`tests/test_cut_round5.py` 那 6 条红是**人写的现场**；这个文件把修法本身
钉住 —— 都是这一轮的修法**新引入或新依赖**的判据，不是重复。

四块：

  ① **删一格**：两侧不许粘成一个词（中文连写行最容易踩）。
  ② **多值格**：写两个词就写两个词，不许整串加引号（那会变成一个
     谁也没声明过的属性）。
  ③ **可选段**：人把固定字（`at`）一起写回来时不许变成 `at at right`。
  ④ **区间来源**：没词的格子必须给"认不出"，不许按值去行里找；
     `@N` 是"第几个吃词的格子"，不是"第几格"（`@N` 用错会改掉语句名）。

跑法::

    python -m pytest tests/test_cut_writeback_spans.py -q
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.authoring import pattern as P                        # noqa: E402
from storygraph.story import spec_args as SA                        # noqa: E402
from storygraph.story import statement_registry as SR               # noqa: E402

SHOW = {'kind': 'keyword', 'text': 'show'}

CN = [SHOW,
      {'kind': 'variable', 'column': '角色', 'options': ['小明']},
      {'kind': 'choice', 'column': '服装', 'options': ['泳装', '校服']},
      {'kind': 'choice', 'column': '表情', 'options': ['开心']}]

OPT = [SHOW,
       {'kind': 'variable', 'column': '角色', 'options': ['lan']},
       {'kind': 'choice', 'column': '表情', 'options': ['happy']},
       {'kind': 'optional', 'column': '位置', 'text': 'at',
        'options': ['left', 'right']}]

MULTI = [SHOW,
         {'kind': 'variable', 'column': '角色', 'options': ['lan']},
         {'kind': 'choice', 'column': '服装', 'options': ['base_swimsuit'],
          'multi': True},
         {'kind': 'choice', 'column': '表情',
          'options': ['happy', 'sad', 'shy'], 'multi': True}]

QUOTED = [SHOW,
          {'kind': 'variable', 'column': '角色', 'options': ['lan']},
          {'kind': 'choice', 'column': '服装', 'options': ['a\\"b']},
          {'kind': 'choice', 'column': '表情', 'options': ['happy']}]

#: 声明里既有固定字格、又有可选段的形状（`@N` 最容易串位的那一种）
CUSTOM = [{'kind': 'keyword', 'text': 'mycue'},
          {'kind': 'variable', 'column': '人物', 'options': ['lan']},
          {'kind': 'optional', 'column': '位置', 'text': 'at',
           'options': ['left', 'right']},
          {'kind': 'choice', 'column': '表情', 'options': ['happy', 'sad']}]


# ===========================================================================
#  ① 删一格
# ===========================================================================

def test_中文连写删中间那一格_两侧不许粘成一个词():
    """
    `show 小明泳装开心` 删「服装」：

        对：show 小明 开心     ← 两个词之间**必须有分隔符**，不然 Ren'Py 读成
                                  `开心` 挂在 `小明` 后面，是两个属性粘一起
        错：show 小明开心     ← 看着像"删干净了"，实际多出一个属性

    英文行不会踩到，因为词之间本来就有空格；中文连写行的分隔符**只有一处**，
    删掉它就把两个幸存者焊在一起了。
    """
    slots = P.parse_pattern(CN)
    assert SA.set_slot_value('show 小明泳装开心', slots, 2, '') == 'show 小明 开心'
    assert SA.set_slot_value('show 小明泳装开心', slots, 1, '') == 'show 泳装开心'
    assert SA.set_slot_value('show 小明泳装开心', slots, 3, '') == 'show 小明泳装'


def test_英文行删一格_只少一个词_空格不多不少():
    slots = P.parse_pattern(MULTI)
    assert SA.set_slot_value('show lan base_swimsuit happy', slots, 2, '') \
        == 'show lan happy'
    assert SA.set_slot_value('show lan base_swimsuit happy', slots, 3, '') \
        == 'show lan base_swimsuit'


# ===========================================================================
#  ② 多值格写多个词
# ===========================================================================

def test_多值格写两个词_是两个词不是一个带空格的词():
    """
    「表情」是多值格（`happy sad` 同一格两个词）。人把它改成 `shy sad`：

        对：show lan base_swimsuit shy sad
        错：show lan base_swimsuit "shy sad"   ← 一个名叫 `shy sad` 的属性，
                                                  游戏里根本不存在，而且不报错
    """
    slots = P.parse_pattern(MULTI)
    assert SA.set_slot_value('show lan base_swimsuit happy sad', slots, 3,
                             'shy sad') == 'show lan base_swimsuit shy sad'


def test_整串正好是一个候选时_仍然算一个词():
    """反方向护栏：候选本身含空格（`base hands down`）时不许拆成三个词。"""
    pat = [SHOW,
           {'kind': 'variable', 'column': '角色', 'options': ['lan']},
           {'kind': 'choice', 'column': '服装',
            'options': ['base hands down', 'base_swimsuit'], 'multi': True},
           {'kind': 'choice', 'column': '表情', 'options': ['happy']}]
    slots = P.parse_pattern(pat)
    assert SA.set_slot_value('show lan base_swimsuit happy', slots, 2,
                             'base hands down') \
        == 'show lan "base hands down" happy'


# ===========================================================================
#  ③ 可选段
# ===========================================================================

@pytest.mark.parametrize('given', ['right', 'at right'])
def test_可选段改值_固定字既不许丢也不许重(given):
    """
    读值那一侧给的是 `at left`（含固定字），界面上也这么显示 —— 人可能把
    `at` 一起写回来。两种写法都必须得到 `at right`：

        丢固定字：show lan happy right      ← `right` 变成一个凭空多出来的属性
        重固定字：show lan happy at at right
    """
    slots = P.parse_pattern(OPT)
    assert SA.set_slot_value('show lan happy at left', slots, 3, given) \
        == 'show lan happy at right'


def test_可选段清空_整段一起删():
    slots = P.parse_pattern(OPT)
    assert SA.set_slot_value('show lan happy at left', slots, 3, '') \
        == 'show lan happy'


def test_可选段原样写回_逐字节不动():
    slots = P.parse_pattern(OPT)
    line = 'show lan happy at left'
    assert SA.set_slot_value(line, slots, 3, 'at left') == line


# ===========================================================================
#  ④ 区间来源
# ===========================================================================

def test_没词的格子必须给认不出_不许按值去行里找():
    """
    护栏：`slot_span` 不许退回"这个值在行里长什么样"。

    `show lan happy` 上「服装」这一格**这一行没有词** —— 它的值在别处
    （`happy` 是表情的候选），按值找会把它认成服装的（那正是第三轮
    `show lan lan happy` 改错格的老病根）。
    """
    slots = P.parse_pattern(MULTI)
    assert SA.slot_span('show lan happy', slots, 2) == ('', '', '')
    assert SA.slot_span('show lan happy', slots, 3) == (9, 14, 'happy')


def test_同一行两个相同的词_区间按格子下标取():
    """`show lan lan happy`：两个 `lan` 同值，靠值找永远给第一个。"""
    same = [SHOW,
            {'kind': 'variable', 'column': '角色', 'options': ['lan']},
            {'kind': 'choice', 'column': '服装', 'options': ['lan']},
            {'kind': 'choice', 'column': '表情', 'options': ['happy']}]
    slots = P.parse_pattern(same)
    assert SA.slot_span('show lan lan happy', slots, 1)[:2] == (5, 8)
    assert SA.slot_span('show lan lan happy', slots, 2)[:2] == (9, 12)


@pytest.mark.parametrize('pattern,line,values,expect', [
    (CN, 'show 小明泳装开心', {1: '小红', 2: '校服', 3: '害羞'},
     'show 小红校服害羞'),
    # ⚠ 写回的值是**解码后**的（`c"d`），落回源码时转义一次 —— 不是两次。
    (QUOTED, 'show lan "a\\"b" happy', {2: 'c"d'},
     'show lan "c\\"d" happy'),
    (MULTI, 'show lan base_swimsuit happy sad', {3: 'shy'},
     'show lan base_swimsuit shy'),
])
def test_逐格改写各改各的(pattern, line, values, expect):
    slots = P.parse_pattern(pattern)
    out = line
    for i, v in values.items():
        out = SA.set_slot_value(out, slots, i, v)
    assert out == expect


def test_没区间时只追加一次_而且再写同一个值不再追加():
    """区间认不出（这一行还没有这一格）→ 追加；第二次写同一个值必须原样。"""
    slots = P.parse_pattern(MULTI)
    once = SA.set_slot_value('show lan', slots, 2, 'base_swimsuit')
    assert once == 'show lan base_swimsuit', once
    assert SA.set_slot_value(once, slots, 2, 'base_swimsuit') == once


# ===========================================================================
#  ⑤ `@N` 是"第几个吃词的格子"，不是"第几格"
# ===========================================================================

def test_at_号要按吃词的格子翻译成格子下标():
    """
    声明 `[固定字 mycue][人物][可选段 at][表情]`：

        吃词的格子：mycue=第0个、人物=第1个、表情=**第2个**（可选段不占号）
        格子下标  ：mycue=0、人物=1、可选段=2、表情=**3**

    直接把 `@2` 当格子下标用 → 改「表情」改的是**可选段**：
    实测 `mycue lan at left happy` → `mycue lan sad happy`（`at left` 没了、
    表情还是 `happy`）。翻译过之后才是 `mycue lan at left sad`。
    """
    slots = P.parse_pattern(CUSTOM)
    assert SA._pattern_slot_index_for(slots, 'mycue·表情@2') == 3
    assert SA._pattern_slot_index_for(slots, 'mycue·人物@1') == 1
    values = {'mycue·表情@2': 'sad', '__pattern__': slots}
    assert SA.fold_dynamic_columns('mycue lan at left happy', values) \
        == 'mycue lan at left sad'


def test_固定字格不许顺序兜底拿走一列():
    """
    声明 `[固定字 swim_suit][人物][服装]`，`columns` 只列两个取值列（最自然的
    写法：固定字不是参数）。兜底如果把固定字格跟「人物」配成一对，位置号就
    整体左移一格，改「人物」会改到**语句名**：

        实测（修之前）：`swim_suit lyj "泳装" mood=happy` → `lyj happy "泳装" mood=happy`

    固定字格只有**名字对得上**时才认领一列。
    """
    rules = {'statements': [{
        'name': 'swim_suit',
        'columns': [{'name': '人物', 'choices': ['lyj', 'ling']},
                    {'name': '服装', 'choices': ['泳装', '校服']}],
        'pattern': [{'kind': 'keyword', 'text': 'swim_suit'},
                    {'kind': 'variable', 'column': '人物',
                     'options': ['lyj', 'ling']},
                    {'kind': 'choice', 'column': '服装',
                     'options': ['泳装', '校服']}],
    }]}
    tmp = Path(__import__('tempfile').mkdtemp(prefix='r5_at_'))
    (tmp / 'storygraph.rules.json').write_text(
        json.dumps(rules, ensure_ascii=False), encoding='utf-8')
    reg = SR.StatementRegistry.for_project(str(tmp), use_cache=False)
    cols = {c['name']: c['positional'] for c in SR.spec_columns(reg, 'swim_suit')}
    assert cols == {'人物': 1, '服装': 2}, cols
    pattern = rules['statements'][0]['pattern']
    out = SA.fold_dynamic_columns(
        'swim_suit lyj "泳装" mood=happy',
        {'swim_suit·人物@1': 'lyj', 'swim_suit·服装@2': '校服',
         '__pattern__': pattern})
    assert out == 'swim_suit lyj 校服 mood=happy', out


# ===========================================================================
#  ⑥ `required` 的"来源"标记
# ===========================================================================

def test_required_的标记不进清单():
    """
    `_required_set` 只是"这句话是谁说的"，落盘那一刻必须摘掉 ——
    清单是人要读、要 diff、要手改的文件，内部标记进那里只是噪声。
    """
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix='r5_req_'))
    pattern = P.with_optional_defaults(
        P.parse_pattern([SHOW,
                         {'kind': 'variable', 'column': '角色',
                          'options': ['ye']},
                         {'kind': 'choice', 'column': '服装',
                          'options': ['base_swimsuit']}]), 'show')
    assert any('_required_set' in s for s in pattern)     # 内存里确实带着
    got = SR.declare_statement(str(tmp), 'show', pattern=pattern)
    assert got['ok'] is True, got
    text = (tmp / 'storygraph.rules.json').read_text(encoding='utf-8')
    assert '_required_set' not in text
    data = json.loads(text)
    slots = data['statements'][0]['pattern']
    assert [s.get('required') for s in slots][1:] == [True, False], slots


def test_存盘再读回_显式必填不会被默认翻掉():
    """
    往返一次（写清单 → 读回来 → 再补默认）之后，人明确勾的"必填"必须还是必填。
    `required` 是具体值，所以"键在 = 说过的" —— 不需要那个标记也能站得住。
    """
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix='r5_req2_'))
    raw = [SHOW,
           {'kind': 'variable', 'column': '角色', 'options': ['ye']},
           {'kind': 'choice', 'column': '服装', 'options': ['base_swimsuit'],
            'required': True},
           {'kind': 'choice', 'column': '表情', 'options': ['happy']}]
    first = P.with_optional_defaults(P.parse_pattern(raw), 'show')
    SR.declare_statement(str(tmp), 'show', pattern=first)
    loaded = json.loads((tmp / 'storygraph.rules.json').read_text(encoding='utf-8'))
    again = P.with_optional_defaults(
        P.parse_pattern(loaded['statements'][0]['pattern']), 'show')
    assert [s.get('required') for s in again][1:] == [True, True, False], again
