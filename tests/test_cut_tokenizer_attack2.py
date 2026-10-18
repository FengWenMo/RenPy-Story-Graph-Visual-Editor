# -*- coding: utf-8 -*-
"""
第二轮攻击：打重做后的「词法层 + 一份 DP + 从 spans 倒推报告」
================================================================================

第一轮 21 条（`test_cut_tokenizer_hell.py`）已全绿，所以这里**不重复**那些：
引号不带引号、两套口径打架、贪心假失败、静默丢词——都修好了。

这一轮从**新结构**里找缝（白盒读完 `_atoms` / `_split_atom` / `_spans` /
`_report` 之后动手，不是拿旧结论套）：

  A. 代价之间**算总账**：偷一个别人的词（8） vs 这一格空着（7）+ 那个词晾着（3）
     → 8 < 10，于是"偷"永远赢。注释里只比了 8 > 7，漏了被偷那个词本来要付的 3。
  B. `order-first`（试切）与 `candidates`（卡片读值）的**分岔**现在会给出不同的
     **格子值** —— 试切显示「服装=junk / 表情=base_swimsuit」，卡片读出
     「服装=base_swimsuit / 表情=happy」。
  C. 词法层把引号剥了、`_tokens` 只返回 `str`，`quoted` 标记就此丢失 →
     写回时含空格的值**不再补引号**，`show lan "base swimsuit" happy` 改一格
     会变成 `show lan my custom outfit happy`（行直接坏掉）。
  D. 小口子：`word_offset='x'` 抛 `ValueError`；`_cell` 的 `ok` 永远为 `True`。

⚠ 仍然不用 `xfail`：红就是红。修好之前这些断言应该一直红。

跑法::

    python -m pytest tests/test_cut_tokenizer_attack2.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.authoring import pattern as P                        # noqa: E402
from storygraph.story import spec_args as SA                        # noqa: E402

SHOW = {'kind': 'keyword', 'text': 'show'}

#: 三格：角色（任意变量）/ 服装 / 表情 —— 后两格都有候选，没有重叠。
PLAIN = [SHOW,
         {'kind': 'variable', 'column': '角色', 'options': ['lan', 'ye']},
         {'kind': 'choice', 'column': '服装', 'options': ['base_swimsuit']},
         {'kind': 'choice', 'column': '表情', 'options': ['happy']}]


def _cells(pattern, line):
    return [(c['column'], c['text']) for c in P.match_line(pattern, line)['cells']]


# ===========================================================================
#  A. 偷词：代价总账算错了
# ===========================================================================

@pytest.mark.parametrize('pattern, line, victim, thief', [
    # 手误多打了一个 lan：第二个 lan 被「服装」当成了自己的值
    ([SHOW,
      {'kind': 'variable', 'column': '角色', 'options': ['lan']},
      {'kind': 'choice', 'column': '服装', 'options': ['base_swimsuit']}],
     'show lan lan', 'lan', '服装'),
    # 中文常态：'的' 是角色候选，缺服装时被服装偷走
    ([SHOW,
      {'kind': 'variable', 'column': '角色', 'options': ['小明', '的']},
      {'kind': 'choice', 'column': '服装', 'options': ['泳装']}],
     'show 小明的', '的', '服装'),
    # 末格缺词：多出来的 lan 被「表情」当成了表情
    ([SHOW,
      {'kind': 'variable', 'column': '角色', 'options': ['lan']},
      {'kind': 'choice', 'column': '服装', 'options': ['base_swimsuit']},
      {'kind': 'choice', 'column': '表情', 'options': ['happy']}],
     'show lan base_swimsuit lan', 'lan', '表情'),
])
def test_一个词不许被别的格子偷走(pattern, line, victim, thief):
    """
    这三行的共同点：**有一个词明确属于别的格子**（`owners` 知道），
    而本格缺词。当前全部 `ok=True`，并且把这个词显示成本格的值：

        show lan lan              → 服装 = lan      （代价 8）
        show 小明的                → 服装 = 的       （代价 8）
        show lan base_swimsuit lan → 表情 = lan      （代价 8）

    人看到绿灯，库里就存下一条不存在的服装/表情。**这是假成功里最贵的一种**：
    不是"切不上"，是"切上了、值是从隔壁抢的"。
    """
    got = P.match_line(pattern, line)
    cells = {c['column']: c['text'] for c in got['cells']}
    assert cells.get(thief) != victim, (cells, got['reason'])
    # 本格缺词就该如实报，而不是拿别人的词顶上
    assert got['ok'] is False, (cells, got['reason'])
    assert victim in got['leftover'] or not got['leftover'], (
        '被偷的词要么算没人认领，要么这一行本来就没有多余的词', got['leftover'])


def test_偷词的代价必须贵于_空着加上晾着():
    """
    白盒：`COST_STOLEN` 比的是 `EMPTY_SLOT`，但"不偷"那一侧还要多付一笔
    `SKIP_WORD`（被偷的词晾在那儿，每词 3 点）：

        偷：            COST_STOLEN            = 8
        不偷：          EMPTY_SLOT + SKIP_WORD = 7 + 3 = 10

    8 < 10 → **偷永远赢**。`EMPTY_SLOT` 的注释写的是"比抢一个别人的词（8）
    略便宜 → 默认能不动别人的词就不动"，只算了 7 > 8 这一半。

    要让"不偷"真的赢，必须 `COST_STOLEN > EMPTY_SLOT + SKIP_WORD`。

    ⚠ 只调这个数会误伤另一条真需求（`mypose happy sad at left`：人物格没有候选，
      希望它把 `happy` 抢来当人名）。那一条的正确修法不是调数字，而是**区分
      "本格是随便写（没有候选）"与"本格是封闭枚举"** —— 前者抢词算自由文本（2），
      后者才算偷（8）。判据见 `test_任意变量格抢词这条需求不许被修坏`。
    """
    assert P.COST_STOLEN > P.EMPTY_SLOT + P.SKIP_WORD, (
        f'COST_STOLEN={P.COST_STOLEN} 不比 EMPTY_SLOT+SKIP_WORD='
        f'{P.EMPTY_SLOT + P.SKIP_WORD} 贵 → 缺一格时必然偷邻格的词')


# ===========================================================================
#  B. 策略分岔：试切说一套、卡片读一套
# ===========================================================================

def test_试切与卡片读值不许对同一行给出不同的格子值():
    """
    `lan junk base_swimsuit happy`（`junk` 谁也不认识）：

        试切（order-first，界面显示的） ：服装=junk      / 表情=base_swimsuit
        卡片（candidates，存进库的）     ：服装=base_swimsuit / 表情=happy

    `POLICIES` 承认"两种策略会分岔"，但分岔的**后果**是同一格两个值：
    用户盯着试切那一栏改顺序，改的是 `junk` 那个位置；库里存的却是另一套。
    这正是"试切说切上了、卡片读出来不一样"的同一个病，只是换了触发条件
    （以前是两套词法，现在是两套策略）。
    """
    slots = P.parse_pattern(PLAIN)[1:]        # 摘掉动作名那一格（`_claim` 的约定）
    shown = {c['column']: c['text'] for c in P.match_line(
        PLAIN, 'show lan junk base_swimsuit happy')['cells']}
    read = SA._claim('lan junk base_swimsuit happy', [dict(s) for s in slots])
    read_by_column = {slot['column']: read.get(i, '')
                      for i, slot in enumerate(slots)}
    assert shown['服装'] == read_by_column['服装'], (shown, read_by_column)
    assert shown['表情'] == read_by_column['表情'], (shown, read_by_column)


def test_中间夹垃圾_不许把真正的候选挤到下一格还报成功():
    """
    `lan junk base_swimsuit`（三格，末格 `happy` 没写）：

        当前：ok=True，角色=lan / 服装=junk / 表情=base_swimsuit

    垃圾 `junk` 占了服装的位置，**真正属于服装的候选被挤到表情去**。
    这一行的正确结论是"第 4 格缺词"（或"junk 没人认领 + 表情缺词"），
    不是绿灯。

    这不是"自由文本合不合法"的口径问题：`base_swimsuit` 的归属是明确的
    （`_owners_of` 写着它属于服装），它出现在表情格里就是**跨格错位**。
    """
    got = P.match_line(PLAIN, 'show lan junk base_swimsuit')
    cells = {c['column']: c['text'] for c in got['cells']}
    assert cells['表情'] != 'base_swimsuit', (cells, got['reason'])
    assert got['ok'] is False, (cells, got['reason'])


# ===========================================================================
#  C. 引号信息在词法层丢掉 → 写回不补引号
# ===========================================================================

def test_写回含空格的值必须补引号():
    """
    「服装」的候选是 `base swimsuit`（带空格，真机立绘就有）。
    把它改成一个同样带空格的新值：

        当前结果：show lan my custom outfit happy     ← 引号没了

    Ren'Py 里这就是三个词/一个不存在的属性 —— **改一格把整行改坏**。
    `set_slot_value` 的等式守卫只管"值没变就逐字节原样"，值一变就裸插文本，
    因为词法层交给它的值**没有带引号**（`_tokens` 只返回 `str`，`quoted` 标记
    在 `_atoms` 里就丢了）。

    ⚠ 修法有两种，选一个：词法层把 `quoted` 带进 `values`（写回据此补引号），
      或者 `set_slot_value` 自己判"含空格且原本是引号串 → 补引号"。
      两种都必须保证：只改这一格，别碰别人。
    """
    line = 'show lan "base swimsuit" happy'
    pattern = P.parse_pattern([SHOW,
                               {'kind': 'variable', 'column': '角色',
                                'options': ['lan']},
                               {'kind': 'choice', 'column': '服装',
                                'options': ['base swimsuit', 'base_hand_up']},
                               {'kind': 'choice', 'column': '表情',
                                'options': ['happy']}])
    out = SA.set_slot_value(line, pattern, 2, 'my custom outfit')
    assert '"my custom outfit"' in out, out
    assert out.startswith('show lan '), out
    assert out.endswith(' happy'), out


# ===========================================================================
#  D. 两个小口子
# ===========================================================================

def test_值不在候选里时格子必须能被标出来():
    """
    `_cell()` 里的 `ok` 是**写死的 True**（`return {..., 'ok': True}`）。
    于是"值不在候选里"这件事在界面结构上**没有位置放**：

        lan 校服 happy → 服装 = 校服（候选只有 base_swimsuit），单元格 ok=True

    `ok` 这个字段存在的意义就是给界面标黄/标红。现在它恒真，等于没有。
    修法：`_cell` 按"值是否在本格候选里"算（多值格按每个词算）。

    ⚠ 这一条原来的例子是 `lan junk base_swimsuit`（服装 = junk）。修 A（不许偷词）
    与新的"跳过垃圾词"之后，那一行的正确答案变成了"服装 = base_swimsuit、
    `junk` 晾着" —— 服装那一格**不再**装着候选外的值。同一个病换一个仍然
    会触发的形状来压，并且顺便压住"别把 ok 改成恒假"。
    """
    got = P.match_line(PLAIN, 'show lan 校服 happy')
    outfit = next(c for c in got['cells'] if c['column'] == '服装')
    assert outfit['text'] == '校服', got['cells']
    assert outfit['ok'] is False, outfit
    ok_cell = next(c for c in P.match_line(
        PLAIN, 'show lan base_swimsuit happy')['cells'] if c['column'] == '服装')
    assert ok_cell['ok'] is True, ok_cell


def test_word_offset_脏输入不许把界面炸掉():
    """
    `int(word_offset or 0)` 没有兜底：传一个字符串就抛 `ValueError`。

    `match_line` 是给界面按键刷新用的（`dialog_learn` 里每次改样例都调），
    而 Qt 槽会吞异常 —— 用户看到的是"点了没反应"，不是报错。
    `word_offset` 来源于界面状态/配置，脏一次就够。
    """
    got = P.match_line(PLAIN, 'show lan base_swimsuit happy', word_offset='x')
    assert got['ok'] is True, got


# ===========================================================================
#  护栏：下面这些**已经对了**，改上面的三条不许把它们弄坏
# ===========================================================================

def test_任意变量格抢词这条需求不许被修坏():
    """
    `mypose happy sad at left`：人物格是**任意变量、没有候选**，
    表情格是多值、候选 `happy/sad`。

    正确结果（用户现场要的）：人物=`happy`、表情=`sad`、位置=`at left`。

    ⚠ 这条与"不许偷词"看似矛盾，区别在**本格有没有候选**：
      没有候选 = 随便写（`happy` 对它是自由文本，代价 2）；
      有候选 = 封闭枚举（别人的候选落进来才算偷，代价 8）。
      修 A 的时候必须保住这条。
    """
    slots = P.parse_pattern([
        {'kind': 'variable', 'column': '人物'},
        {'kind': 'choice', 'column': '表情', 'options': ['happy', 'sad'],
         'multi': True},
        {'kind': 'optional', 'column': '位置', 'text': 'at'}])
    got = P.assign_tokens(['happy', 'sad', 'at', 'left'], slots)
    assert got['values'] == {0: 'happy', 1: 'sad', 2: 'at left'}, got['values']
    assert got['exact'] is True, got


def test_垃圾词仍然要如实晾着():
    """第一轮修好的那条：末尾垃圾不许被多值格吃掉。"""
    pattern = [SHOW,
               {'kind': 'variable', 'column': '角色', 'options': ['lan'],
                'multi': True},
               {'kind': 'choice', 'column': '服装',
                'options': ['base_swimsuit'], 'multi': True},
               {'kind': 'choice', 'column': '表情', 'options': ['happy'],
                'multi': True}]
    got = P.match_line(pattern, 'show lan base_swimsuit happy junkword')
    assert got['ok'] is False, got
    assert all('junkword' not in c['text'] for c in got['cells']), got['cells']
    assert got['leftover'] == ['junkword'], got


def test_拼错的关键字仍然不许流进第一个值格():
    """第一轮修好的：`shw` 该晾着，不该变成角色名。"""
    got = P.match_line(PLAIN, 'shw lan base_swimsuit happy')
    assert got['ok'] is False, got
    assert 'shw' not in got['cells'][1]['text'], got['cells']
    assert got['leftover'] == ['shw'], got


def test_引号里的连词仍然是一个不带引号的值():
    """第一轮修好的：`"base swimsuit"` → 服装 = `base swimsuit`（无引号）。"""
    pattern = [SHOW,
               {'kind': 'variable', 'column': '角色', 'options': ['lan']},
               {'kind': 'choice', 'column': '服装',
                'options': ['base swimsuit']},
               {'kind': 'choice', 'column': '表情', 'options': ['happy']}]
    got = P.match_line(pattern, 'show lan "base swimsuit" happy')
    assert got['ok'] is True, got
    assert got['cells'][2]['text'] == 'base swimsuit', got['cells']
