# -*- coding: utf-8 -*-
"""
第五轮攻击：写回的**区间来源**，与 `required` 默认的**入口顺序**
================================================================================

前四轮 63 条全绿（切分逻辑本身已经相当硬）。这一轮从"切分之后"打：

  ① **中文无空格行的写回全坏**（P0）：
     `show 小明泳装开心` 改任何一格 → `show 小明泳装开心 小红`（**行尾追加**）。
     词法层明明切出了 4 个词（`token_spans` 给的字符区间也全对），
     但 `spec_args.slot_span` 找区间用的是 `positional_tokens` —— 它按**空白**切，
     这一行只有 2 个 token（`show` / `小明泳装开心`），于是找不到"泳装" →
     兜底"追加"。更贵的是**幂等**：把三格读出来的值原样写回，
     整行会被追加一遍（`show 小明泳装开心 小明 泳装 开心`）。

  ② `with_optional_defaults` **跟输入顺序有关**（P0）：
     传**原始 dict** → 补出 `required=False`，`show ye happy` 判绿；
     传 `parse_pattern(...)` 的结果 → 全 True，同一行判红。
     根因：判据是 `'required' not in slot`，而 `parse_pattern` 会给每个格子
     补上 `required: True` —— 于是"只补缺"永远补不上。任何"先 parse 再补"
     的调用方（读盘后的声明、测试、别的入口）都静默失效。

  ③ 转义值写回**层数翻倍**（P1）：
     `"a\\"b"` 读出的值是 `a\\"b`，原样写回变成 `"a\\\\\\"b"` ——
     每往返一次多一层反斜杠。

⚠ 仍然不用 `xfail`。红=缺，绿=护栏。

跑法::

    python -m pytest tests/test_cut_round5.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.authoring import pattern as P                        # noqa: E402
from storygraph.story import spec_args as SA                        # noqa: E402

SHOW = {'kind': 'keyword', 'text': 'show'}

CN = [SHOW,
      {'kind': 'variable', 'column': '角色', 'options': ['小明']},
      {'kind': 'choice', 'column': '服装', 'options': ['泳装', '校服']},
      {'kind': 'choice', 'column': '表情', 'options': ['开心']}]
CN_LINE = 'show 小明泳装开心'

RAW = [SHOW,
       {'kind': 'variable', 'column': '角色', 'options': ['ye']},
       {'kind': 'choice', 'column': '服装', 'options': ['base_swimsuit']},
       {'kind': 'choice', 'column': '表情', 'options': ['happy']}]

QUOTED = [SHOW,
          {'kind': 'variable', 'column': '角色', 'options': ['lan']},
          {'kind': 'choice', 'column': '服装', 'options': ['a\\"b']},
          {'kind': 'choice', 'column': '表情', 'options': ['happy']}]
QUOTED_LINE = 'show lan "a\\"b" happy'


# ===========================================================================
#  ① 中文无空格行的写回
# ===========================================================================

def test_中文无空格行_改一格只换那一格():
    """
    词法层切得出来（`tokens = show/小明/泳装/开心`），
    `token_spans` 的字符区间也全对：`[(0,4), (5,7), (7,9), (9,11)]`。

    但 `set_slot_value` 找区间走的是 `slot_span → positional_tokens`（按空白切），
    这一行只有 2 个 token → 找不到"泳装" → 兜底**追加**：

        show 小明泳装开心 校服      ← 人改的是「服装」，结果行尾多一个值

    正确：区间来源必须与词法层**同源**（`pattern.token_spans` + `_claim_spans`
    的词下标），不能再用一套"按空白切"的词法。
    """
    slots = P.parse_pattern(CN)
    assert SA.set_slot_value(CN_LINE, slots, 1, '小红') == 'show 小红泳装开心'
    assert SA.set_slot_value(CN_LINE, slots, 2, '校服') == 'show 小明校服开心'
    assert SA.set_slot_value(CN_LINE, slots, 3, '害羞') == 'show 小明泳装害羞'


def test_中文无空格行_值没变时逐字节原样():
    """
    把三格读出来的值**原样**写回一遍，行必须一个字节都不动。

    当前：`show 小明泳装开心 小明 泳装 开心` —— 打开表再导出一遍，
    一行变两行；这是"不归己的行逐字节不动"那条铁律的直接违反。
    """
    slots = P.parse_pattern(CN)
    values = P.assign_tokens(P.tokenize(CN_LINE, slots), slots)['values']
    out = CN_LINE
    for i in (1, 2, 3):
        out = SA.set_slot_value(out, slots, i, values[i])
    assert out == CN_LINE, out


# ===========================================================================
#  ② required 默认的入口顺序
# ===========================================================================

def test_可选属性格的默认_与输入有没有被_parse_过无关():
    """
    `show` / `scene` / `hide` 的属性格默认"可不填"（`show ye happy` 合法）。

    `with_optional_defaults` 的判据是 `'required' not in slot`，但
    `parse_pattern` 会给每个格子补 `required: True` —— 于是：

        传原始 dict      → required = [True, True, False, False] → `show ye happy` 绿
        传 parse 过的    → required = [True, True, True, True]  → 同一行红

    这不是"调用方自己写错了"：读盘后的声明、别的入口拿到的都是 parse 过的形状，
    而 `match_line` 自己也会 parse 一遍。默认值必须**与入口顺序无关**。
    """
    raw = P.with_optional_defaults([dict(s) for s in RAW], 'show')
    parsed = P.with_optional_defaults(P.parse_pattern(RAW), 'show')
    assert [s.get('required') for s in raw][2:] == [False, False], raw
    assert [s.get('required') for s in parsed][2:] == [False, False], parsed
    assert P.match_line(parsed, 'show ye happy')['ok'] is True


def test_显式写了必填的格子_不许被默认覆盖():
    """
    反方向护栏：格子明确写了 `required: True` 时，`with_optional_defaults`
    不许把它改成 False（"只补缺"）。`parse_pattern` 之后要能区分
    "显式 True" 与 "默认补的 True" —— 这正是上一条要求的那个标记。
    """
    explicit = [dict(s) for s in RAW]
    explicit[2]['required'] = True
    got = P.with_optional_defaults(P.parse_pattern(explicit), 'show')
    assert got[2].get('required') is True, got[2]


# ===========================================================================
#  ③ 转义值写回
# ===========================================================================

def test_写回值没变时不许把转义层数翻倍():
    """
    `show lan "a\\"b" happy` 里那一格的值是 `a\\"b`（反斜杠是内容）。

    原样写回当前得到 `"a\\\\\\"b"` —— 反斜杠 1 个变 3 个。
    每打开表再导出一次就多一层，最后剧本里的字符串彻底变样。
    """
    slots = P.parse_pattern(QUOTED)
    value = P.assign_tokens(P.tokenize(QUOTED_LINE, slots), slots)['values'][2]
    assert value == 'a\\"b', value
    assert SA.set_slot_value(QUOTED_LINE, slots, 2, value) == QUOTED_LINE


# ===========================================================================
#  ④ 写回的区间不能"按值找"
# ===========================================================================

def test_改可选段_固定字_at_不许丢():
    """
    `show lan happy at left` 改「位置」→ `right`：

        当前：show lan happy right      ← `at` 没了
        正确：show lan happy at right

    读值那一侧是对的（`位置 = at left`，含固定字），写回却把**整段**换成了
    新值 —— 于是 `right` 变成一个凭空多出来的属性，`at` 消失，语句语义全变。
    可选段的写回必须"保留固定字、只换值"；删值时才整段删。
    """
    opt = [SHOW,
           {'kind': 'variable', 'column': '角色', 'options': ['lan']},
           {'kind': 'choice', 'column': '表情', 'options': ['happy']},
           {'kind': 'optional', 'column': '位置', 'text': 'at',
            'options': ['left', 'right']}]
    slots = P.parse_pattern(opt)
    assert SA.set_slot_value('show lan happy at left', slots, 3,
                             'right') == 'show lan happy at right'
    # 删值 → 整段删掉（`at` 也一起走，这是对的）
    assert SA.set_slot_value('show lan happy at left', slots, 3,
                             '') == 'show lan happy'


def test_同一行两个相同的词_改哪一格就换哪一个():
    """
    `show lan lan happy`：第一个 `lan` 是角色，第二个是服装（两格候选同名）。

        改服装 → 当前 `show base_swimsuit lan happy`   ← 改的是**角色**那一格
        正确   → `show lan base_swimsuit happy`

    根因：`slot_span` 按**值**在行里找区间（找到第一个 `lan`）。
    值相同的时候，"第几格"这条信息只能从 `_claim_spans` 的词下标来 ——
    这也是中文拆词那条（①）要的同一个修法：**按格子下标取区间，不按值找**。
    """
    same = [SHOW,
            {'kind': 'variable', 'column': '角色', 'options': ['lan']},
            {'kind': 'choice', 'column': '服装', 'options': ['lan']},
            {'kind': 'choice', 'column': '表情', 'options': ['happy']}]
    slots = P.parse_pattern(same)
    assert SA.set_slot_value('show lan lan happy', slots, 2,
                             'base_swimsuit') == 'show lan base_swimsuit happy'
    assert SA.set_slot_value('show lan lan happy', slots, 1,
                             'ye') == 'show ye lan happy'


# ===========================================================================
#  护栏
# ===========================================================================

def test_多值格改写要整段替换():
    """`happy sad` 是同一格的两个值，改它必须两个词一起换掉。"""
    multi = [SHOW,
             {'kind': 'variable', 'column': '角色', 'options': ['lan']},
             {'kind': 'choice', 'column': '服装',
              'options': ['base_swimsuit'], 'multi': True},
             {'kind': 'choice', 'column': '表情',
              'options': ['happy', 'sad', 'shy'], 'multi': True}]
    slots = P.parse_pattern(multi)
    assert SA.set_slot_value('show lan base_swimsuit happy sad', slots, 3,
                             'shy') == 'show lan base_swimsuit shy'


def test_空格版中文行照旧改一格只换一格():
    line = 'show 小明 泳装 开心'
    slots = P.parse_pattern(CN)
    assert SA.set_slot_value(line, slots, 2, '校服') == 'show 小明 校服 开心'
    assert SA.set_slot_value(line, slots, 1, '小红') == 'show 小红 泳装 开心'


def test_引号串照旧幂等():
    line = 'show lan "base swimsuit" happy'
    raw = [SHOW,
           {'kind': 'variable', 'column': '角色', 'options': ['lan']},
           {'kind': 'choice', 'column': '服装', 'options': ['base swimsuit']},
           {'kind': 'choice', 'column': '表情', 'options': ['happy']}]
    slots = P.parse_pattern(raw)
    values = P.assign_tokens(P.tokenize(line, slots), slots)['values']
    out = line
    for i in (1, 2, 3):
        out = SA.set_slot_value(out, slots, i, values[i])
    assert out == line, out


def test_空引号那一格仍然不参与接住():
    """第四轮的行为不许被这一轮的写回/默认改动带坏。"""
    p = P.with_optional_defaults([dict(s) for s in RAW], 'show')
    got = P.assign_tokens(P.tokenize('show ye "" base_swimsuit happy', p), p)
    assert got['broken'] == [2], got['broken']
    assert got['values'].get(2) == '', got['values']
    assert got['values'].get(3) == 'base_swimsuit', got['values']
    assert got['leftover'] == ['happy'], got['leftover']
