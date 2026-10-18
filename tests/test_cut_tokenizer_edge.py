# -*- coding: utf-8 -*-
"""
切行「词法 + 全局分配」的**变态边界**（2026-10-17 第三轮，用户点名的六条）
================================================================================

与已有两份的分工：

  · `test_cut_tokenizer.py`      —— 用户现场那一行（三格各归各位）的回归钉子；
  · `test_cut_tokenizer_hell.py` —— 白盒账单（预算归属、代价数字、两套口径）；
  · **本文件**                   —— 把"引号 / 脏空格 / 相邻多选 / 死槽位"四条
    各往**极端**推：不问"能不能切"，只问"切错的时候会不会骗我"。

每一条都对着用户 2026-10-17 的原话：

  一、引号包裹 = 一个词，且值里不许带引号        → `_atoms`
  二、脏空格（Tab / 全角 / 零宽 / BOM）不产生空串 → `_atoms` + `_split_atom`
  三、相邻多选不许互相抢词，也不许把垃圾焊进值   → `_spans` 的 `multi`
  四、死槽位（没写字的固定段）**一个词都不许吃**
  五、报错必须**同时**说清"第几个词 × 第几格"
  六、试切必须一瞬间（备忘录，不许暴力回溯）

跑法::

    python -m pytest tests/test_cut_tokenizer_edge.py -q
"""

from __future__ import annotations

import random
import re
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.authoring import pattern as P                        # noqa: E402
from storygraph.story import spec_args as SA                        # noqa: E402

SHOW = {'kind': 'keyword', 'text': 'show'}
ROLES = ['lan', 'ye', 'mm']
FACE = ['afraid', 'angry', 'happy', 'sad']
#: 真机立绘的属性名**本身带空格**（`layeredimage` 的 `base hands down`）
PHRASE = ['base swimsuit', 'base_hand_up']

QUOTED = [SHOW,
          {'kind': 'variable', 'column': '角色', 'options': ROLES},
          {'kind': 'choice', 'column': '服装', 'options': PHRASE},
          {'kind': 'choice', 'column': '表情', 'options': FACE}]


def _cells(pattern, line, **kw):
    return [(c['column'], c['text']) for c in P.match_line(pattern, line,
                                                           **kw)['cells']]


def _tok(line, pattern):
    """复刻 `match_line` 的取词口径（候选 + 固定字一起交给 `_tokens`）。"""
    opts = []
    for s in P.parse_pattern(pattern):
        opts.extend(s.get('options') or ())
        if s.get('text'):
            opts.append(s['text'])
    return P._tokens(line, options=opts)


# ===========================================================================
#  一、引号：**一个词**，而且值里不许带引号
# ===========================================================================

def test_引号里的连续脏空格被压成一个():
    """`"base\t  swimsuit"` 的引号内部也要归一 —— 否则对不上候选 `base swimsuit`。"""
    line = 'show lan "base\t  swimsuit" happy'
    assert _cells(QUOTED, line)[2] == ('服装', 'base swimsuit'), _cells(QUOTED, line)


def test_引号里的全角空格和不换行空格也一样():
    for gap in ('\u3000', '\u00a0', '\u200b', '\ufeff'):
        line = f'show lan "base{gap}swimsuit" happy'
        assert _cells(QUOTED, line)[2] == ('服装', 'base swimsuit'), repr(line)


def test_单引号同样是一个词():
    line = "show lan 'base swimsuit' happy"
    got = P.match_line(QUOTED, line)
    assert got['ok'] is True, got
    assert got['cells'][2]['text'] == 'base swimsuit', got['cells']


def test_空引号不许变成一个空词_但要占住位置():
    """
    `show lan "" happy` 里的空引号**不是值**（Ren'Py 里 `""` 是空属性名，
    跑不起来），所以它不许在词流里变成一个空串 —— 但**必须占住那个位置**：
    不占位，后面的值整体左移一格（`服装=happy`），还报绿灯。

    ⚠ 这条判据 2026-10-18（第三轮攻击）**改过口径**：旧版钉的是"必须消失"，
      而"消失"正是左移的来源。现在它消失成 `EMPTY_TOKEN` 占位符，既不进值、
      也占位置，报错还会点名"空引号"。
    """
    toks = _tok('show lan "" happy', QUOTED)
    assert toks == ['show', 'lan', P.EMPTY_TOKEN, 'happy'], toks
    assert '' not in toks, '空串会混进值里'


def test_引号没关上要报出是第几个词():
    """少一个闭引号 → 后半行会被当成一个词；这句话必须**先于**"切不上"说出来。"""
    got = P.match_line(QUOTED, 'show lan "base swimsuit')
    assert got['ok'] is False, got
    assert '第 3 个词' in got['reason'], got['reason']
    assert '引号' in got['reason'] and '没关上' in got['reason'], got['reason']


def test_引号里的词归它自己那一格_前面那格抢不走():
    """`"happy"` 是表情的候选：上面的多值格再贪也不能把它焊进自己的值。"""
    pattern = [{'kind': 'choice', 'column': '服装', 'options': ['clothes2'],
                'multi': True},
               {'kind': 'choice', 'column': '表情', 'options': ['happy'],
                'multi': True}]
    got = P.match_line(pattern, '"happy"')
    assert [(c['column'], c['text']) for c in got['cells']] == [
        ('服装', ''), ('表情', 'happy')], got['cells']
    assert got['ok'] is True, got


def test_引号里的词只算一个词_报错序号不能错位():
    """
    `"base swimsuit"` 是**第 3 个词** —— 后面那个多余的词就是**第 5 个**。

    如果词法把它拆成两个（`base` / `swimsuit`），报错会指着第 6 个词，
    而那一行根本没有第 6 个词：人照着数字去改，改的是别处。
    """
    got = P.match_line(QUOTED, 'show lan "base swimsuit" happy junk')
    assert got['ok'] is False, got
    assert '第 5 个词「junk」' in got['reason'], got['reason']


def test_引号里的转义不许把词切坏():
    tokens = _tok('show lan "a\\"b" happy', QUOTED)
    assert tokens[2] == 'a\\"b', tokens
    assert len(tokens) == 4, tokens


def test_全角引号和书名号是普通字符_不许剥掉():
    """
    `mycue 「闪电」` 里那两个括号是**内容的一部分**（Ren'Py 只认 `"` / `'`）。

    ⚠ 把它们当引号剥掉 = 悄悄改台词 —— 比切不上更坏的静默损坏。
    """
    pattern = [{'kind': 'keyword', 'text': 'mycue'},
               {'kind': 'variable', 'column': '内容'}]
    got = P.match_line(pattern, 'mycue 「闪电」')
    assert got['ok'] is True, got
    assert got['cells'][1]['text'] == '「闪电」', got['cells']


def test_引号里的值改回去要整段换掉_不许在行尾追加():
    """
    切行修好了不算完：**改一格**也得认得出那一整段引号。

    以前 `slot_span` 用 `text.split()` 找区间，`"base swimsuit"` 被拆成
    `"base` / `swimsuit"` 两个带引号的半截 → 找不到 → 退回"追加"，
    实测把改一格写成 `show lan "base swimsuit" base_hand_up`（旧值还在，行尾又冒一个）。
    """
    line = 'show lan "base swimsuit" happy'
    assert SA.set_slot_value(line, QUOTED, 2, 'base_hand_up') == \
        'show lan base_hand_up happy'
    # 值没变 → **逐字节原样**（打开表再导出一个字节都不动）
    assert SA.set_slot_value(line, QUOTED, 2, 'base swimsuit') == line
    assert SA.set_slot_value(line, QUOTED, 3, 'sad') == \
        'show lan "base swimsuit" sad'
    # 删掉那一格 → 两个词之间不能粘在一起
    assert SA.set_slot_value(line, QUOTED, 2, '') == 'show lan happy'


# ===========================================================================
#  二、脏空格：连续多个 / Tab / 全角 / 零宽 / BOM
# ===========================================================================

@pytest.mark.parametrize('sep', ['\t', '\u3000', '\u00a0', '\u200b', '\ufeff',
                                 '\n', '   ', ' \t\u3000 '])
def test_各种看不见的空白都只是分隔符(sep):
    line = sep.join(['show', 'lan', '"base swimsuit"', 'happy'])
    got = P.match_line(QUOTED, line)
    assert got['ok'] is True, (repr(line), got)
    assert [(c['column'], c['text']) for c in got['cells']][1:] == [
        ('角色', 'lan'), ('服装', 'base swimsuit'), ('表情', 'happy')]


def test_不同脏空格切出来的词必须完全一样():
    """归一化必须是**幂等**的：换一种脏空格，词表一个都不许变。"""
    base = ['show', 'lan', '"base swimsuit"', 'happy']
    variants = [' '.join(base), '\t'.join(base), '\u3000'.join(base),
                '  '.join(base), '\n'.join(base),
                '\u200b'.join(base), ' \t\u3000 '.join(base)]
    got = {tuple(_tok(v, QUOTED)) for v in variants}
    assert len(got) == 1, got
    assert got.pop() == ('show', 'lan', 'base swimsuit', 'happy')


def test_只有空白的行不许崩():
    got = P.match_line(QUOTED, ' \t\u3000\u200b ')
    assert got['ok'] is False, got
    assert got['reason'], got
    assert [c['text'] for c in got['cells']] == ['', '', '', ''], got['cells']


def test_脏空格拼出来的行里不许出现空词():
    """`split(' ')` 会给你一串空串 —— 这里必须一个都没有。"""
    rng = random.Random(20261017)
    seps = [' ', '  ', '\t', '\u3000', '\u00a0', '\u200b', '\n', '']
    words = ['show', 'lan', 'happy', '"base swimsuit"', '', 'x', '校园']
    for _ in range(300):
        parts = [rng.choice(words) for _ in range(rng.randint(0, 6))]
        line = ''.join(w + rng.choice(seps) for w in parts)
        tokens = _tok(line, QUOTED)
        assert '' not in tokens, (repr(line), tokens)
        assert all(str(t).strip() for t in tokens), (repr(line), tokens)


# ===========================================================================
#  三、相邻多选：不许互相抢，也不许把垃圾焊进值
# ===========================================================================

def test_三个多值格连排_中间那格空着也合法():
    pattern = [SHOW,
               {'kind': 'variable', 'column': 'A', 'options': ROLES,
                'multi': True},
               {'kind': 'choice', 'column': 'B', 'options': ['base_swimsuit'],
                'multi': True},
               {'kind': 'choice', 'column': 'C', 'options': FACE,
                'multi': True}]
    got = P.match_line(pattern, 'show lan happy')
    assert got['ok'] is True, got
    assert [c['text'] for c in got['cells']] == ['show', 'lan', '', 'happy'], \
        got['cells']


def test_相邻多值格候选重叠时一个词只进一格():
    pattern = [{'kind': 'choice', 'column': 'A',
                'options': ['happy', 'sad'], 'multi': True},
               {'kind': 'choice', 'column': 'B',
                'options': ['happy', 'sad'], 'multi': True}]
    assert [c['text'] for c in P.match_line(pattern,
                                            'happy happy')['cells']] == [
        'happy', 'happy']
    raw = P.assign_tokens(['happy', 'happy'], P.parse_pattern(pattern))
    assert raw['spans'] == {0: (0, 1), 1: (1, 2)}, raw['spans']


def test_夹在中间的垃圾词不许被多值格吃进值里():
    """
    `clothes2 junk happy`：`junk` 谁也不认识。

    多值格只吃**自己人**，所以它停在 `clothes2`；后面的格子宁可**跳过**这个
    垃圾词去认 `happy`，也不许把 `junk` 焊成 `junk happy` 这种值。
    """
    pattern = [SHOW,
               {'kind': 'variable', 'column': '角色', 'options': ROLES},
               {'kind': 'choice', 'column': '服装',
                'options': ['clothes2', 'base_swimsuit'], 'multi': True},
               {'kind': 'choice', 'column': '表情', 'options': FACE,
                'multi': True}]
    got = P.match_line(pattern, 'show lan clothes2 junk happy')
    assert [c['text'] for c in got['cells']][1:] == ['lan', 'clothes2', 'happy'], \
        got['cells']
    assert got['leftover'] == ['junk'], got['leftover']
    assert '第 4 个词「junk」' in got['reason'], got['reason']


def test_多值格不许把后面的固定字吃进来():
    """`happy sad at left` 里的 `at left` 属于可选段，多值格必须在 `at` 前停。"""
    pattern = [{'kind': 'variable', 'column': '人物'},
               {'kind': 'choice', 'column': '表情', 'options': ['happy', 'sad'],
                'multi': True},
               {'kind': 'optional', 'text': 'at', 'column': '位置',
                'options': ['left']}]
    got = P.match_line(pattern, 'lan happy sad at left')
    eaten = {c['column']: c['text'] for c in got['cells']}
    assert eaten == {'人物': 'lan', '表情': 'happy sad', '位置': 'at left'}, eaten
    assert got['ok'] is True, got


# ===========================================================================
#  四、死槽位：没写字的固定段，一个词都不许吃
# ===========================================================================

def test_死关键字不许把下一格的词当成自己的值():
    """
    第 1 格是"固定几个字"却没写是哪几个字 —— 它永远对不上。

    这时它**一个词都不该吃**（旧实现会让它跳过，于是 `show` 流到第 2 格，
    变成一个角色名）；报错也要先说这一格。
    """
    pattern = [{'kind': 'keyword'},
               {'kind': 'variable', 'column': '角色', 'options': ROLES}]
    got = P.match_line(pattern, 'show lan')
    assert got['ok'] is False, got
    assert '没写"是哪几个字"' in got['reason'], got['reason']
    assert got['cells'][1]['text'] == 'lan', got['cells']
    assert '第 1 格' in got['reason'], got['reason']


def test_死可选段一个词都不吃_后面的格子照样各归各位():
    pattern = [{'kind': 'variable', 'column': 'A'},
               {'kind': 'optional', 'column': '位置'},
               {'kind': 'variable', 'column': 'B'}]
    got = P.match_line(pattern, 'x y')
    assert got['ok'] is True, got
    assert [c['text'] for c in got['cells']] == ['x', '', 'y'], got['cells']


def test_勾了限死却没候选_要点名是第几格():
    pattern = [{'kind': 'choice', 'column': '人', 'limit': True}]
    got = P.match_line(pattern, '任何人都行')
    assert got['ok'] is False, got
    assert got['rejected'] == 0, got
    assert got['rejected_word'] == '任何人都行', got
    assert '第 1 格' in got['reason'], got['reason']
    assert '只能从这些里选' in got['reason'], got['reason']


# ===========================================================================
#  五、报错精确到词：第几个词 × 第几格（用户第四条）
# ===========================================================================

def test_关键字对不上_要说第几个词():
    got = P.match_line([SHOW, {'kind': 'variable', 'column': '角色',
                               'options': ROLES}], 'shw lan')
    assert got['ok'] is False, got
    assert '第 1 格' in got['reason'], got['reason']
    assert '第 1 个词' in got['reason'], got['reason']
    assert 'shw' in got['reason'], got['reason']


def test_没人认领的词_要说第几个词():
    got = P.match_line([SHOW, {'kind': 'variable', 'column': '角色',
                               'options': ROLES}], 'show lan junk')
    assert got['ok'] is False, got
    assert '第 3 个词「junk」' in got['reason'], got['reason']
    assert '没人认领' in got['reason'], got['reason']


def test_少了词_要说哪一格在哪个词的位置上缺():
    pattern = [SHOW,
               {'kind': 'variable', 'column': '角色', 'options': ROLES},
               {'kind': 'choice', 'column': '服装', 'options': ['base_swimsuit']}]
    got = P.match_line(pattern, 'show lan')
    assert got['ok'] is False, got
    assert '第 3 格' in got['reason'], got['reason']
    assert '第 3 个词' in got['reason'], got['reason']
    assert '该有一个词' in got['reason'], got['reason']


def test_被限死挡下的词_要说第几个词():
    pattern = [{'kind': 'variable', 'column': '角色', 'options': ROLES},
               {'kind': 'choice', 'column': '表情', 'options': FACE,
                'limit': True}]
    got = P.match_line(pattern, 'lan junk')
    assert got['ok'] is False, got
    assert got['rejected'] == 1, got
    assert '第 2 格' in got['reason'], got['reason']
    assert '第 2 个词' in got['reason'] and 'junk' in got['reason'], got['reason']


def test_报错点名的格子永远真的是空的():
    """四种失败的 reason 里那个"第 N 格"，必须真的是一格空着的（不许自相矛盾）。"""
    cases = [
        ([SHOW, {'kind': 'variable', 'column': '角色', 'options': ROLES}],
         'shw lan'),
        ([SHOW, {'kind': 'variable', 'column': '角色', 'options': ROLES},
          {'kind': 'choice', 'column': '服装', 'options': ['base_swimsuit']}],
         'show lan'),
        ([{'kind': 'variable', 'column': '角色', 'options': ROLES},
          {'kind': 'choice', 'column': '表情', 'options': FACE, 'limit': True}],
         'lan junk'),
    ]
    for pattern, line in cases:
        got = P.match_line(pattern, line)
        m = re.search(r'第 (\d+) 格', got['reason'])
        assert m, (line, got['reason'])
        i = int(m.group(1)) - 1
        assert not str(got['cells'][i]['text']).strip(), (
            f'报错点名第 {i + 1} 格是空的，但界面显示「{got["cells"][i]["text"]}」',
            line, got['reason'])


# ===========================================================================
#  六、试切必须一瞬间（备忘录，不许暴力回溯）
# ===========================================================================

def test_三百个词的试切不许卡死():
    """
    300 个词、5 格、其中 4 格是"可有多个"且**每个词都在它们的候选里**
    —— 这是 `_spans` 的最坏形状（每一格都要枚举"吃几个"）。

    ⚠ 朴素回溯在这里是 `O(分支^格子数)`（早就卡死）；备忘录之后是
      `O(格子数 × 词数²)`，300 词实测 ~0.15 秒。判据卡 1.5 秒防的是
      "某次改动把它变回指数/雪崩"。
    """
    vocab = [f'w{i}' for i in range(300)]
    pattern = [SHOW] + [{'kind': 'choice', 'column': f'c{i}', 'options': vocab,
                         'multi': True} for i in range(4)]
    words = ['show'] + vocab
    t0 = time.time()
    got = P.assign_tokens(words, P.parse_pattern(pattern))
    cost = time.time() - t0
    assert cost < 1.5, f'300 词试切 {cost:.2f}s'
    assert got['exact'] is True, got['cost']
    # 区间不许重叠、不许越界（性能再好也不能拿正确性换）
    used: set = set()
    for a, b in got['spans'].values():
        assert not (set(range(a, b)) & used), got['spans']
        used |= set(range(a, b))


def test_同一行切两次结果必须一模一样():
    edges = ['show lan "base swimsuit" happy',
             'show\tlan base swimsuit happy junk',
             'show lan "" happy',
             '  show   lan  ',
             'shw lan "x y"']
    for line in edges:
        assert P.match_line(QUOTED, line) == P.match_line(QUOTED, line), line


# ===========================================================================
#  七、多引擎通用：这套切法不绑 Ren'Py 的词表
# ===========================================================================

def test_换个引擎的动作名照样切():
    """只要声明了格子，`bg … fade …` 这种完全不同的语句也得切得对。"""
    pattern = [{'kind': 'keyword', 'text': 'bg'},
               {'kind': 'choice', 'column': '场景',
                'options': ['town square', 'forest']},
               {'kind': 'keyword', 'text': 'fade'},
               {'kind': 'choice', 'column': '时长', 'options': ['0.5', '1.0']}]
    got = P.match_line(pattern, 'bg "town square" fade 1.0')
    assert got['ok'] is True, got
    assert [c['text'] for c in got['cells']] == ['bg', 'town square', 'fade',
                                                 '1.0'], got['cells']


def test_三路切法对同一行给出同一个答案():
    """
    试切（`match_line`）/ 分配器（`assign_tokens`）/ 卡片读值（`slot_value_map`）
    —— **同一行、同一个答案**。三者分家过一次（"试切说切上了、卡片读出来不一样"），
    引号和脏空格最容易把它们再分一次。
    """
    lines = [
        'show lan "base swimsuit" happy',
        'show\tlan   "base swimsuit"\u3000happy',
        'show lan base swimsuit happy',          # 没引号：两个词拼成候选
    ]
    slots = P.parse_pattern(QUOTED)
    for line in lines:
        got = P.match_line(QUOTED, line)
        assert got['ok'] is True, (line, got)
        trial = {c['column']: c['text'] for c in got['cells'] if c['column']}
        mapped = SA.slot_value_map(line, slots)
        assert trial == {k: v for k, v in mapped.items() if k in trial}, (
            line, trial, mapped)
        raw = P.assign_tokens(_tok(line, QUOTED), slots)
        for i, (a, b) in raw['spans'].items():
            assert raw['values'][i] == got['cells'][i]['text'], (
                line, i, raw['values'], got['cells'][i]['text'])
