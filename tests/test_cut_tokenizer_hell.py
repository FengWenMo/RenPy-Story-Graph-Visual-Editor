# -*- coding: utf-8 -*-
"""
切行「分词 + 代价路由」的地狱测试 —— **这个文件当前是账单，红的就是缺口**
================================================================================

被审的对象（2026-10-17 重构，尚未提交）：
`storygraph/authoring/pattern.py` 的 `_atoms` / `_split_atom` / `_tokens` /
`_owners_of` / `_spans` / `_report` / `assign_tokens` / `match_line`。

> ⚠ 这份账单是**第一次重构之后**写的（当时那版叫 `_exact_spans` / `_assign_report`，
> 两套口径并存）。**现在已全部还清**：`_spans` 只有一份分配，`_report` 从那一份
> 倒推四类失败信号 —— 所以下面那些"两套口径打架"的判据现在应当**全绿**，
> 它们留下来是为了钉住"不许再长出第二套口径"。

⚠ 本文件**不用 `xfail`**（项目立场见 `test_hell_custom_module.py` 开头）：
   `xfail` 会把"又坏了"变成"预期内的"。这里每一条都是**硬断言**，红就是
   当前实现真的没做到，修好之前它应该一直红。

分六段，对应验收前点名的四条隐患 + 两条我顺手查出来的：

  一、硬不变量          —— 任何输入都必须成立（除了报错定位那条）
  二、隐患一 中文分词   —— "无空格才回退"到底在什么条件下失效
  三、隐患二 引号       —— 不是"没做"，是"做了但把引号写进值里还报成功"
  四、隐患三 代价数字   —— 谁被粘进谁、临界点在哪
  五、隐患四 报错定位   —— 两套口径互相打架的实证
  六、顺手查出来的     —— `candidates` 静默丢词 / 长句耗时

跑法::

    python -m pytest tests/test_cut_tokenizer_hell.py -q
"""

from __future__ import annotations

import random
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.authoring import pattern as P                        # noqa: E402
from storygraph.story import spec_args as SA                        # noqa: E402

SHOW = {'kind': 'keyword', 'text': 'show'}
ROLES = ['lan', 'ye', 'mm']
OUTFIT = ['clothes2', 'base_swimsuit', 'base_hands_down']
FACE = ['afraid', 'angry', 'happy', 'sad']


# ---------------------------------------------------------------------------
#  小工具
# ---------------------------------------------------------------------------

def _three(*, role_multi=True, outfit_multi=True, face_multi=False,
           outfit_options=None, face_options=None):
    return [SHOW,
            {'kind': 'variable', 'column': '角色', 'options': ROLES,
             'multi': role_multi},
            {'kind': 'choice', 'column': '服装',
             'options': list(outfit_options or OUTFIT), 'multi': outfit_multi},
            {'kind': 'choice', 'column': '表情',
             'options': list(face_options or FACE), 'multi': face_multi}]


def _cells(pattern, line):
    return [(c['column'], c['text']) for c in P.match_line(pattern, line)['cells']]


def _tokens_of(pattern, line):
    """复刻 `match_line` 的取词口径（候选 + 固定字一起交给 `_tokens`）。"""
    slots = P.parse_pattern(pattern)
    options = []
    for s in slots:
        options.extend(s.get('options') or [])
        if s.get('text'):
            options.append(s['text'])
    return P._tokens(line, options=options), slots


def _allowed(slot):
    return [str(o) for o in (slot.get('options') or [])]


def _cross_talk(pattern, line):
    """
    一格的值里**含有别的格子的候选**（按子串，因为中文粘连会把两段焊成一个词）。

    这是"角色抢服装词"的一般化判据：不管成功还是失败，界面上都不该出现
    「角色 = 小明泳装」这种把邻格候选焊进自己值的显示。
    值本身等于那个候选（两格候选同名）不算。
    """
    got = P.match_line(pattern, line)
    slots = P.parse_pattern(pattern)
    hits = []
    for i, cell in enumerate(got['cells']):
        value = str(cell['text'] or '').strip()
        if not value:
            continue
        for j, other in enumerate(slots):
            if j == i:
                continue
            for opt in _allowed(other):
                if len(opt) >= 2 and opt != value and opt in value:
                    hits.append((i + 1, value, j + 1, opt))
    return hits


# ===========================================================================
#  一、硬不变量：跟"切得对不对"无关，任何输入都必须成立
# ===========================================================================

def _random_case(rng):
    words = ['lan', 'ye', 'happy', 'sad', 'base_swimsuit', 'clothes2',
             'junk', '小明', '泳装', '开心', 'at', 'left', 'x', 'y', 'ab', 'cd']
    n_slot = rng.randint(1, 5)
    pattern = []
    for i in range(n_slot):
        kind = rng.choice(['variable', 'choice', 'choice', 'keyword',
                           'optional'])
        slot = {'kind': kind, 'column': f'c{i}',
                'options': rng.sample(words, rng.randint(0, 3))}
        if kind in ('keyword', 'optional'):
            slot['text'] = rng.choice(words)
        if rng.random() < 0.4:
            slot['multi'] = True
        if rng.random() < 0.25:
            slot['limit'] = True
        pattern.append(slot)
    toks = [rng.choice(words) for _ in range(rng.randint(0, 6))]
    sep = rng.choice([' ', '  ', '\t', ''])
    return pattern, sep.join(toks)


def test_任何输入的分配区间都不许重叠或越界():
    """一个词不能同时进两个格子 —— 这是"不许越界抢占"的最底层判据。"""
    rng = random.Random(20261017)
    for _ in range(1500):
        pattern, line = _random_case(rng)
        tokens, slots = _tokens_of(pattern, line)
        got = P.assign_tokens(tokens, slots)
        for tag in ('spans', 'best_spans'):
            seen = set()
            for si, span in (got.get(tag) or {}).items():
                a, b = span
                assert 0 <= a < b <= len(tokens), (tag, si, span, tokens, line)
                used = set(range(a, b))
                assert not (used & seen), (tag, si, span, tokens, line)
                seen |= used


def test_成功那一屏的每个值都必须来自它自己的词区间():
    """
    `ok=True` 时界面显示的值，必须**恰好**是分配结果里那几个词拼出来的。

    这一条防的是"试切说切上了、卡片读出来不一样"的同款：界面与分配不同源。
    """
    rng = random.Random(20261018)
    for _ in range(1500):
        pattern, line = _random_case(rng)
        tokens, slots = _tokens_of(pattern, line)
        got = P.assign_tokens(tokens, slots)
        shown = P.match_line(pattern, line)
        if not shown['ok']:
            continue
        assert len(shown['cells']) == len(slots), line
        for i, cell in enumerate(shown['cells']):
            span = got['spans'].get(i)
            want = ' '.join(tokens[span[0]:span[1]]) if span else ''
            assert cell['text'] == want, (i, cell['text'], want, line)


def test_同一输入两次结果必须完全一样():
    rng = random.Random(20261019)
    for _ in range(600):
        pattern, line = _random_case(rng)
        assert P.match_line(pattern, line) == P.match_line(pattern, line), line


def test_格子数量永远等于声明数量():
    """少一个格子 = 界面下标整体错位（人越改越错）。"""
    rng = random.Random(20261020)
    for _ in range(600):
        pattern, line = _random_case(rng)
        got = P.match_line(pattern, line)
        assert len(got['cells']) == len(pattern), (line, len(got['cells']))


# ===========================================================================
#  二、隐患一：中文"无空格才回退"的边界
# ===========================================================================

CN = [SHOW,
      {'kind': 'variable', 'column': '角色', 'options': ['小明', '小红']},
      {'kind': 'choice', 'column': '服装', 'options': ['泳装', '校服']},
      {'kind': 'choice', 'column': '表情', 'options': ['开心', '害羞']}]


def test_整行无空格的中文仍要切对():
    """护栏：这是重构已经做到的那一半，别修新缺口时把它弄坏。"""
    assert _cells(CN, 'show小明泳装开心') == [
        ('', 'show'), ('角色', '小明'), ('服装', '泳装'), ('表情', '开心')]


def test_只要出现一个空格_粘连的那一段也要按候选切开():
    """
    `_tokens` 的取舍是"有空格就整行 `split()`，一个都不再按候选补切"。

    后果：`show 小明 泳装开心` 里 `泳装开心` 被当成**一个词**，
    「服装」吃掉一个不存在的值、「表情」空着 —— 报错还说"第 4 格没词"，
    真正的原因（中文没空格）一个字都没提。
    """
    assert _cells(CN, 'show 小明 泳装开心') == [
        ('', 'show'), ('角色', '小明'), ('服装', '泳装'), ('表情', '开心')]


def test_混写时界面不许把邻格的候选焊进这一格():
    """
    `show 小明 泳装开心` → 当前界面显示「服装 = 泳装开心」。

    `泳装` 是服装候选、`开心` 是表情候选，两段被焊成一个词塞进服装格。
    不论切得上切不上，这种"一格吃掉邻格候选"的显示都不该出现。
    """
    assert _cross_talk(CN, 'show 小明 泳装开心') == []


def test_混写且后两格可多值时_不许静默给出不存在的值():
    """
    同一行，把「服装 / 表情」勾成"可有多个" —— 界面就**报成功**了：

        ok=True, 服装='', 表情='泳装开心'

    `泳装开心` 既不是服装候选也不是表情候选，是最坏的一种：绿灯 + 错值。
    """
    pattern = [SHOW,
               {'kind': 'variable', 'column': '角色', 'options': ['小明']},
               {'kind': 'choice', 'column': '服装', 'options': ['泳装', '校服'],
                'multi': True},
               {'kind': 'choice', 'column': '表情', 'options': ['开心'],
                'multi': True}]
    got = P.match_line(pattern, 'show 小明 泳装开心')
    assert got['ok'] is True, got
    assert [(c['column'], c['text']) for c in got['cells']][1:] == [
        ('角色', '小明'), ('服装', '泳装'), ('表情', '开心')], got['cells']


def test_贪心最长匹配不许把可解的切成切不上():
    """
    三格：`ab` / `cd` / （可出现可不出现的）`abcd`，行是无空格的 `abcd`。

    `_tokens` 按"最长优先"先命中 `abcd`（它是第三格的候选），于是只剩一个词
    → 报第 2 格没词。而 `ab` + `cd`（第三格不出现）本来是**唯一的正确切法**。

    这是"分词用贪心、路由用全局 DP"的结构性矛盾：DP 拿到的是**已经被贪心
    定死**的词，再全局也救不回来。
    """
    pattern = [{'kind': 'choice', 'column': 'A', 'options': ['ab']},
               {'kind': 'choice', 'column': 'B', 'options': ['cd']},
               {'kind': 'optional', 'column': 'C', 'text': 'zz',
                'options': ['abcd']}]
    got = P.match_line(pattern, 'abcd')
    assert got['ok'] is True, got
    assert [(c['column'], c['text']) for c in got['cells']] == [
        ('A', 'ab'), ('B', 'cd'), ('C', '')], got['cells']


# ===========================================================================
#  三、隐患二：引号不是"没做"，是"做了但把引号写进值里"
# ===========================================================================

QUOTED = [SHOW,
          {'kind': 'variable', 'column': '角色', 'options': ROLES},
          {'kind': 'choice', 'column': '服装',
           'options': ['base swimsuit', 'base_hand_up']},
          {'kind': 'choice', 'column': '表情', 'options': FACE}]


def test_引号里的连词算一个值_且值里不许带引号():
    """
    `show lan "base swimsuit" happy` 当前 `ok=True`，但「服装」的值是

        '"base swimsuit"'   ← 前后各带一个引号

    这个值会被原样写回 `.rpy`，变成 `show lan "base swimsuit" happy`？
    不 —— 写回去的是**带引号的属性名**，跟策划要的不是一回事。
    比"切不上"更危险：它报成功。
    """
    got = P.match_line(QUOTED, 'show lan "base swimsuit" happy')
    assert got['ok'] is True, got
    assert [(c['column'], c['text']) for c in got['cells']][2] == (
        '服装', 'base swimsuit'), got['cells']


def test_单引号同样不许进值():
    got = P.match_line(QUOTED, "show lan 'base swimsuit' happy")
    assert [(c['column'], c['text']) for c in got['cells']][2] == (
        '服装', 'base swimsuit'), got['cells']


def test_引号里的词不在候选里要如实报():
    """
    把 `wrong thing` 用引号包起来之后，当前照样 `ok=True`：
    `"wrong"` 不在候选里、`"thing"` 也不在，但两词 span 的代价只是 2+2，
    而"整行必须吃完"逼着它接受。

    **限死格（limit=True）的语义在这里被绕开了**：本来该报"候选外的词"。
    """
    locked = [QUOTED[0], QUOTED[1],
              dict(QUOTED[2], limit=True), QUOTED[3]]
    got = P.match_line(locked, 'show lan "wrong thing" happy')
    assert got['ok'] is False, got


def test_试切与卡片读值对同一行必须给出同一个值():
    """
    试切那一侧（`match_line`）不去引号，读值那一侧（`_claim` + `_unquote`）去。

    这正是"试切说切上了、卡片读出来不一样"的同款分叉 —— 同一行，两个答案。
    """
    head = 'lan "base swimsuit" happy'
    slots = P.parse_pattern(QUOTED)[1:]
    claimed = SA._claim(head, slots)
    read_side = SA._unquote(str(claimed.get(1) or ''))
    shown = P.match_line(QUOTED, 'show lan "base swimsuit" happy')
    assert read_side == shown['cells'][2]['text'], (read_side,
                                                   shown['cells'][2]['text'])


# ===========================================================================
#  四、隐患三：代价数字
# ===========================================================================

def test_偷一个词的代价必须严格贵于三个自由词():
    """
    白盒判据（不算行为，算**数字之间的关系**）：

        COST_STOLEN=6，COST_UNKNOWN=2  →  偷 1 个词 = 3 个谁都不认识的词

    正好**相等**。相等意味着"总代价最小"在这一点上没有答案，
    赢家由 `offer()` 的尝试顺序决定 —— 而尝试顺序是 `range(0, n)`，
    也就是"先试少吃"。换一个格子声明顺序、换一个候选长度，答案就会翻。

    要让"不偷"永远赢，偷的代价必须**严格**大于三个自由词；
    或者把这条临界写进文档并给出为什么可以接受。
    """
    assert P.COST_STOLEN > 3 * P.COST_UNKNOWN, (
        f'COST_STOLEN={P.COST_STOLEN} 与 3×COST_UNKNOWN='
        f'{3 * P.COST_UNKNOWN} 同价：平局靠"先试哪个"来断')


def test_多值格不许把不认识的词当成值():
    """
    `show lan dirty base_swimsuit happy`：`dirty` 谁也不认识。

    要防的是「服装」= `dirty base_swimsuit` —— 一个合法候选被一个垃圾词焊住了，
    这个值写进库就是一条**不存在的服装**。

    更难看的是它跟"脏词放在后面"不对称：`lan clothes2 junk happy` 反而切得对
    （服装=`clothes2`）。同一个'垃圾'，放前放后两个结果 —— 说明这条边界是
    **由平局 + 尝试顺序**决定的，不是由规则决定的。

    ⚠ 2026-10-17 定稿（修完再看，原来这条的期望自相矛盾）：
      它同时要求 `ok is True` **和**"`dirty` 不许出现在任何格子里"—— 而
      `ok=True` 的定义就是"每个词都被某一格认领了"。两个要求不可能同时成立。
      真正该守的是后半句（**不许焊接**）：`dirty` 晾着、由 reason 点名
      "第 3 个词「dirty」没人认领"，于是 `ok` 必然是 `False`。
      实现上这条靠两件事：① 多值格只吃**自己人**（候选里的词），垃圾词不吃；
      ② 多值格允许跳过前面的垃圾词（`SKIP_JUNK`），好让自己那串候选从头开始。
    """
    pattern = [SHOW,
               {'kind': 'variable', 'column': '角色', 'options': ROLES,
                'multi': True},
               {'kind': 'choice', 'column': '服装', 'options': OUTFIT,
                'multi': True},
               {'kind': 'choice', 'column': '表情', 'options': FACE,
                'multi': True}]
    got = P.match_line(pattern, 'show lan dirty base_swimsuit happy')
    # 垃圾词要么被单独报出来，要么晾着；绝不能变成某一格值的一部分
    assert all('dirty' not in c['text'] for c in got['cells']), got['cells']
    assert [c['text'] for c in got['cells']][1:] == ['lan', 'base_swimsuit',
                                                     'happy'], got['cells']
    assert got['ok'] is False, got
    assert got['leftover'] == ['dirty'], got['leftover']
    assert '第 3 个词' in got['reason'] and 'dirty' in got['reason'], got['reason']


def test_三个多值格连在一起时_干净数据要各归各位():
    """
    用户点名的形状：**三个格子都勾了"可有多个"**。

    `lan base_swimsuit happy sad` → 角色 / 服装 / 表情(happy sad)。
    这条是"多点吃几个"的仲裁，当前靠 `COST_EXTRA` 与"先试少吃"共同决定。
    """
    pattern = [SHOW,
               {'kind': 'variable', 'column': '角色', 'options': ROLES,
                'multi': True},
               {'kind': 'choice', 'column': '服装', 'options': OUTFIT,
                'multi': True},
               {'kind': 'choice', 'column': '表情', 'options': FACE,
                'multi': True}]
    assert _cells(pattern, 'show lan base_swimsuit happy sad')[1:] == [
        ('角色', 'lan'), ('服装', 'base_swimsuit'), ('表情', 'happy sad')]


# ===========================================================================
#  五、隐患四：报错定位与界面显示互相打架
# ===========================================================================

def test_报错点名的格子必须真的是空的():
    """
    `_assign_report` 走单趟认领（多值格用"尽力吃满"），`best_spans` 走
    partial 全局搜索 —— 两套口径。于是会出现：

        reason: "第 2 格（c1）该有一个词，但这一行里没有"
        cells : 第 2 格 = "base"      ← 明明有值

    用户看到的报错**指向一个已经填好的格子**。这不是"冤枉别的格子"，
    是"报错和画面互相否定"，比冤枉更难自查。

    这条用固定种子的随机搜索找反例（1500 例，实测有十余条）。
    """
    rng = random.Random(20261017)
    bad = []
    for _ in range(1500):
        pattern, line = _random_case(rng)
        got = P.match_line(pattern, line)
        if got['ok'] or '该有一个词' not in got['reason']:
            continue
        m = re.search(r'第 (\d+) 格', got['reason'])
        if not m:
            continue
        i = int(m.group(1)) - 1
        if i < len(got['cells']) and str(got['cells'][i]['text']).strip():
            bad.append((line, got['reason'], got['cells']))
    assert bad == [], f'{len(bad)} 条例：{bad[:3]}'


def test_失败时两套口径必须给出同一套切法():
    """
    `show` 拼成 `shw`：

      · `spans`（进 reason / 进界面）→ 角色吃 **2 个词** `shw lan`（旧版）
      · `best_spans`（partial，进界面）→ 角色吃 **3 个词** `shw lan base_swimsuit`

    同一个失败屏，两处对"角色吃了几个词"给出不同答案（第一次重构那版的病）。

    ⚠ 现在**只有一份分配**：`best_spans` 就是 `spans` 的副本。这条断言留着，
      是为了"谁再想加第二条路径"时当场红。
    """
    got = P.assign_tokens(['shw', 'lan', 'base_swimsuit', 'happy'],
                          P.parse_pattern(_three()))
    assert got['exact'] is False, got
    assert got['spans'] == got['best_spans'], (got['spans'],
                                               got['best_spans'])


def test_拼错的关键字不许被塞进第一个值格():
    """
    `shw lan base_swimsuit happy` 的失败屏把拼错的关键字 `shw` 当成角色名：

        角色 = "shw lan" ｜ 服装 = base_swimsuit ｜ 表情 = happy
        reason: 第 1 格要的关键字是「show」，对不上

    人有两条互相矛盾的线索：一边说"关键字错了"，一边把错的关键字
    当成一个角色值显示出来。
    """
    got = P.match_line(_three(), 'shw lan base_swimsuit happy')
    assert got['ok'] is False, got
    assert 'shw' not in got['cells'][1]['text'], got['cells']


# ===========================================================================
#  六、顺手查出来的两条
# ===========================================================================

def test_跳过垃圾词时被跳过的词必须算没人认领():
    """
    分配器允许"跳过中间那些**谁也不认识**的词去认后面的候选"（把候选当词典）。

    ⚠ 2026-10-17 第二轮攻击起这条不再属于某个 `policy`：跳过进了代价表，
    试切与卡片用的是同一份实现（`POLICIES` / `policy=` 已经删掉）。

    跳过本身没错，但**被跳过的词必须算没人认领**。
    曾经它既不进 `spans`，也不进 `leftover`（`used` 直接算满）：

        tokens = show lan junk1 junk2 base_swimsuit
        spans  = {0: show, 1: lan, 2: base_swimsuit}   ← junk1/junk2 消失
        used   = 5                                      ← 假装全吃完了

    `used` 是 `match_line` 判"后面还有没有词没人认领"的依据 —— 这里被喂了假数。
    """
    slots = P.parse_pattern([SHOW,
                             {'kind': 'variable', 'column': '角色'},
                             {'kind': 'choice', 'column': '服装',
                              'options': ['base_swimsuit']}])
    tokens = ['show', 'lan', 'junk1', 'junk2', 'base_swimsuit']
    got = P.assign_tokens(tokens, slots)
    covered = sum(b - a for a, b in got['spans'].values())
    eaten = {i for a, b in got['spans'].values() for i in range(a, b)}
    dropped = [w for i, w in enumerate(tokens) if i not in eaten]
    assert covered == got['used'], (
        f'spans 只覆盖 {covered} 个词，used 却说 {got["used"]} 个 —'
        f' 被丢掉的：{dropped}')


def test_两百个词的试切不许指数爆炸():
    """
    长句护栏（不是精确性能指标）：`_exact_spans` 的 multi 分支是
    `for length in range(0, m-ti+1)`，每个 length 再算一次 `_span_cost`。
    300 词实测 ~1.4s，说明复杂度还在可控范围；这里卡 2s 防的是
    "某次改动把它变成指数/雪崩"。
    """
    words = ['show'] + [f'w{i}' for i in range(200)]
    # 前 4 个词塞成候选，让 multi 分支真的被走到
    pattern = _three(outfit_options=['w1', 'w2'], face_options=['w3'])
    t0 = time.time()
    P.assign_tokens(words, P.parse_pattern(pattern))
    cost = time.time() - t0
    assert cost < 2.0, f'200 词试切 {cost:.2f}s'
