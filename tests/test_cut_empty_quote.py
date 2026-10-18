# -*- coding: utf-8 -*-
"""
第三轮攻击：空引号（`""` / `''` / `"   "`）
================================================================================

起因（用户 2026-10-18 的观察项）：
    `show lan "" happy` 的空引号被 `_atoms` 丢掉 → 报错里的“第几个词”比人数的少 1。

继续查下去，这条比“少 1”贵得多（下面 ①–④ 是**修之前**实测到的四个后果，
现在全部钉住了；每一条判据都保留着，因为它们就是这一轮的口径）：

  ① **值整体前移**：`show lan "" base_swimsuit happy` 当时 `ok=True`，
     服装=`base_swimsuit`、表情=`happy` —— 人写的是 4 个位置（第 2 个是空），
     工具把它当成 3 个值，**绿灯**。
  ② **报错指向错的格子**：`show "" lan happy` 当时说“第 3 格（服装）该有一个词”，
     实际缺的是第 2 格（角色）——人照它改会越改越错。
  ③ 读值侧（`_claim`）同样前移 → 卡片/落盘跟着错（同源修好了，但同源错）。
  ④ 写回侧（`positional_tokens`）**保留** `""`，切分侧**丢掉** → 两侧词数不同，
     `slot_span` 的格子下标与切分结果错位，`set_slot_value` 会把 `""` 留在行里。

而“词号偏”其实**不只空引号**：`show 小明泳装`（人按空格数 2 个词）当时报
“第 4 个词”——`_split_atom` 把一段拆成两个 token，人按空格只数到一个。
这是中文常态。所以“透传人的第几个词”应该以 **atom（空格分隔的一段）** 为单位，
不是 token。

## 修成的口径（2026-10-18）

  · 空引号 = **输入错误**（Ren'Py 里 `""` 是空属性名，跑不起来），不是“这一格留空”
    —— 想留空就别写那个词（`show lan happy` → 服装空）。
  · 但它**占住那个位置**：词流里是 `pattern.EMPTY_TOKEN`，谁认领它谁就是那一格
    “这一行没有值”；后面的值一格都不许前移（也不许被跳过）。
  · 报错点名“空引号”、给出**人敲的第几个词**，并说清“删掉它 / 改成值”。

⚠ 仍然不用 `xfail`。红=缺，绿=护栏。

跑法::

    python -m pytest tests/test_cut_empty_quote.py -q
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
PLAIN = [SHOW,
         {'kind': 'variable', 'column': '角色', 'options': ['lan']},
         {'kind': 'choice', 'column': '服装', 'options': ['base_swimsuit']},
         {'kind': 'choice', 'column': '表情', 'options': ['happy']}]

#: 这些字里都"没有值"：空串、空格、Tab、全角空格。引号类型也算两种。
EMPTY_QUOTES = ['""', "''", '"   "', '"\t"', '"\u3000"']


def _cells(pattern, line):
    return {c['column']: c['text'] for c in P.match_line(pattern, line)['cells']}


# ===========================================================================
#  ① 前移：最贵的那一条
# ===========================================================================

def test_空引号不许让后面的值整体前移():
    """
    人写的：`show lan "" base_swimsuit happy`
            （角色=lan｜服装=空｜表情=base_swimsuit｜末尾多一个 happy）

    修之前：`""` 被吞 → 3 个词对 3 个值格 → `ok=True`，
            服装=`base_swimsuit`、表情=`happy`（**假绿灯**）。

    现在：服装停在空引号那里（空着 + 点名空引号），`base_swimsuit` 归表情，
          `happy` 算多出来没人认领。**不许绿灯**。
    """
    got = P.match_line(PLAIN, 'show lan "" base_swimsuit happy')
    cells = {c['column']: c['text'] for c in got['cells']}
    assert cells['服装'] != 'base_swimsuit', (cells, got['reason'])
    assert got['ok'] is False, (cells, got['reason'])


def test_空引号在开头不许让角色格顶替它():
    """
    人写的：`show "" lan happy`（角色=空｜服装=lan｜表情=happy）

    修之前：角色=`lan`、服装空 → 报“第 3 格（服装）缺词”。
    人照这句去补服装，把真正缺的**角色**留在空里。

    ⚠ 占位之后 `lan` 也**不该**晾着：`lan` 是角色的候选，而角色那格这一行
      是空的 —— 它对这个位置来说是**没人要**的词（不是"抢"），按位置落到
      服装才是对的（判据后面 `_claim` 那一条是同一件事）。
    """
    got = P.match_line(PLAIN, 'show "" lan happy')
    cells = {c['column']: c['text'] for c in got['cells']}
    assert cells['角色'] == '', (cells, got['reason'])
    assert got['ok'] is False, (cells, got['reason'])


# ===========================================================================
#  ② 必须报成"空引号"，而不是"某一格缺词"
# ===========================================================================

@pytest.mark.parametrize('quote', EMPTY_QUOTES)
def test_任何形态的空引号都要被点名(quote):
    """
    `""` / `''` / `"   "` / `"\\t"` / `"　"` 都是"引号里没有值"。

    修之前 `_atoms` 里 `if text:` 把它们一起丢掉，`problems` 也是空的 ——
    这一对引号在工具里**完全不存在**，于是只能报成"第 3 格缺词"。
    """
    line = f'show lan {quote} happy'
    got = P.match_line(PLAIN, line)
    assert got['ok'] is False, got
    assert ('空引号' in got['reason'] or '没有值' in got['reason']), got['reason']


def test_整行只有一个空引号也要说清根因():
    """`""` 修之前被报成“第 1 格要的关键字 show 对不上”——根因一个字没提。"""
    got = P.match_line(PLAIN, '""')
    assert '空引号' in got['reason'] or '没有值' in got['reason'], got['reason']


def test_空引号宁可红_也不许静默当成这一格留空():
    """
    这一条是把口径写死：`""` **不是**"这一格我留空"的合法写法。

    理由：Ren'Py 的 `show lan "" happy` 里 `""` 是一个空属性名，跑不起来；
    想留空就直接不写那个词（`show lan happy` → 服装空，已经有绿测覆盖）。

    所以空引号必须是**输入错误**：红 + 说清"删掉它或填上内容"。
    """
    got = P.match_line(PLAIN, 'show lan "" happy')
    assert got['ok'] is False, got
    assert '删掉' in got['reason'] or '填上' in got['reason'], got['reason']


# ===========================================================================
#  ③ 读值侧 / 写回侧
# ===========================================================================

def test_读值侧不许把空引号后面的值吸到前一格():
    """
    `_claim` 看不到 `lex_problems`（它只拿 `assign_tokens` 的 `values`）。
    修之前切分侧前移，读值侧也前移：

        `lan "" base_swimsuit happy` → {角色:lan, 服装:base_swimsuit, 表情:happy}

    卡片与落盘都跟着错。**空引号必须在词法层就变成"一格空"**，
    两侧才不会各错各的。
    """
    slots = P.parse_pattern(PLAIN)[1:]
    claimed = SA._claim('lan "" base_swimsuit happy', [dict(s) for s in slots])
    assert claimed.get(1) in (None, ''), claimed
    assert claimed.get(2) == 'base_swimsuit', claimed


def test_写回不许把空引号留在行里():
    """
    写回侧用的是 `positional_tokens`，它**保留** `""` 这个 token；
    切分侧以前 `_atoms` **丢掉**它 —— 两侧词数不同，`slot_span` 的格子下标
    与切分结果错位：

        set_slot_value('show lan "" happy', 2, 'base_swimsuit')
        → 'show lan "" base_swimsuit'      ← 空引号留在行里，还把 happy 顶掉了

    正确：人写的是 4 个位置，第 3 个是空引号、第 4 个是 happy。
    改第 3 格（服装）→ `show lan base_swimsuit happy`（只换那一格，不碰 happy）。
    """
    pattern = P.parse_pattern(PLAIN)
    out = SA.set_slot_value('show lan "" happy', pattern, 2, 'base_swimsuit')
    assert '""' not in out, out
    assert out == 'show lan base_swimsuit happy', out


# ===========================================================================
#  ④ 词号偏移的真正来源（不关空引号）
# ===========================================================================

def test_报错的词号必须按人空格数的词来数():
    """
    `show 小明泳装` —— 人按空格数：**2 个词**（`show` / `小明泳装`）。

    修之前报“第 4 个词的位置”“这一行共 3 个词”：`_split_atom` 把
    `小明泳装` 拆成两个 token，于是 token 序号冒充了“人的词号”。

    这条与空引号无关，是**中文连写的常态**。修法是把 atom 序号透传到报错
    （`_tokens_meta` 的 `words` / `n_words`）。
    """
    pattern = [SHOW,
               {'kind': 'variable', 'column': '角色', 'options': ['小明']},
               {'kind': 'choice', 'column': '服装', 'options': ['泳装']},
               {'kind': 'choice', 'column': '表情', 'options': ['开心']}]
    line = 'show 小明泳装'
    got = P.match_line(pattern, line)
    people_words = len(line.split())
    assert f'共 {people_words} 个词' in got['reason'], got['reason']
    assert '第 4 个词' not in got['reason'], got['reason']


# ===========================================================================
#  护栏：非空引号一个都不许被上面改动带坏
# ===========================================================================

def test_引号里的连词照旧是一个值():
    pattern = [SHOW,
               {'kind': 'variable', 'column': '角色', 'options': ['lan']},
               {'kind': 'choice', 'column': '服装',
                'options': ['base swimsuit']},
               {'kind': 'choice', 'column': '表情', 'options': ['happy']}]
    got = P.match_line(pattern, 'show lan "base swimsuit" happy')
    assert got['ok'] is True, got
    assert got['cells'][2]['text'] == 'base swimsuit', got['cells']


def test_引号内连续空白照旧压成一个空格():
    pattern = [SHOW,
               {'kind': 'variable', 'column': '角色', 'options': ['lan']},
               {'kind': 'choice', 'column': '服装',
                'options': ['base swimsuit']},
               {'kind': 'choice', 'column': '表情', 'options': ['happy']}]
    got = P.match_line(pattern, 'show lan "  base   swimsuit  " happy')
    assert got['ok'] is True, got
    assert got['cells'][2]['text'] == 'base swimsuit', got['cells']


def test_引号没关上照旧报得清楚():
    got = P.match_line(PLAIN, 'show lan "base swimsuit happy')
    assert got['ok'] is False, got
    assert '没关上' in got['reason'], got['reason']


def test_转义引号照旧原样保留():
    """`"a\\"b"` 里的 `\\"` 是内容，不许解码、不许当成结束引号。"""
    got = P.match_line(PLAIN, 'show lan "a\\"b" happy')
    assert got['cells'][2]['text'] == 'a\\"b', got['cells']


# ===========================================================================
#  ⑤ 修完之后补的护栏（2026-10-18 收口时加，不是用户那八条）
# ===========================================================================

def test_空引号那一行永远算没切上():
    """
    `exact` 一旦为真，落盘/导入那一侧就会把它当成"切好了" —— 而 `""` 在
    Ren'Py 里跑不起来。所以 `broken` 非空时 `exact` 必须是假（`ok` 只多加
    一层 `lex_problem` 保险）。
    """
    slots = P.parse_pattern(PLAIN)
    got = P.assign_tokens(P.tokenize('show lan "" happy', slots), slots)
    assert got['broken'] == [2], got['broken']
    assert got['exact'] is False, got


def test_占位符一个字都不许漏给界面():
    """`\\x00` 是内部占位符：格子值 / 没人认领的词 / reason 里都不许出现。"""
    got = P.match_line(PLAIN, 'show lan "" happy')
    assert all(P.EMPTY_TOKEN not in str(c['text']) for c in got['cells'])
    assert all(P.EMPTY_TOKEN not in str(w) for w in got['leftover'])
    assert P.EMPTY_TOKEN not in got['reason'], got['reason']


def test_没改就不许动字节_清空一个已经是空引号的格子也不算改():
    """
    与 `set_spec_arg` / `set_positional_arg` 同一条约定：值没变 → **逐字节原样**。

    ⚠ 这一条挡的是"顺手把 `""` 删掉"：删掉确实能让行变好，但那是**改**，
      必须由人明确做（界面上把那一格清空 ≠ 同一个值）。
    """
    pattern = P.parse_pattern(PLAIN)
    line = 'show lan "" happy'
    assert SA.set_slot_value(line, pattern, 2, '') == line
