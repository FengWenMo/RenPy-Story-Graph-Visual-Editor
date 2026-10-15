"""
「一行词怎么分到格子里」**只有一份实现**（2026-10-15 抽 `pattern.assign_tokens`）
================================================================================

背景（2026-10-14 评审记下的结构性病根）：同一件事在这之前有**三套并行实现** ——

  · `authoring/pattern.match_line`        —— 试切，递归回溯、会"切不上"；
  · `story/spec_args._claim`              —— 卡片读值那一侧的单趟认领；
  · `story/spec_args.slot_value_map`      —— 又把 `_claim` 抄了一遍（换返回形状）。

规则漂移的代价是用户看得见的那种自相矛盾：**试切说切上了、卡片读出来不一样**。

这一份判据管三件事：

  ① **同一口径**：策略不冲突的形状上，"试切"与"读值"必须给出**逐格相同**的分配；
  ② **分岔只有两处、而且都是明写的**（见 `DIVERGENCE`）：枚举格的"候选跳过"、
     以及"引号只在读值侧去掉"。它们写在 `pattern.POLICIES` 与函数说明里，
     由本文件钉住 —— 再长出新分岔就会被抓；
  ③ **失败信号是"报告"而不是"猜"**：关键字对不上 / 勾了限死来了外词 /
     有格子没词 / 有词没人认领，四类各自报出来。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.authoring import pattern as P                      # noqa: E402
from storygraph.story import spec_args as SA                       # noqa: E402


#: 策略不冲突的形状（试切 vs 读值 必须**逐格相同**）。
AGREE_CASES = [
    # 顺序 + 类型
    ([{'kind': 'keyword', 'text': 'show'},
      {'kind': 'variable', 'column': '角色'},
      {'kind': 'choice', 'column': '服装', 'options': ['泳装', '校服']},
      {'kind': 'choice', 'column': '神态', 'options': ['开心', '害羞']}],
     'show 小明 泳装 开心',
     {'角色': '小明', '服装': '泳装', '神态': '开心'}),
    # 多值格 + 后面还跟着可选段：`at left` 必须归可选段，不许被多值格吃掉
    ([{'kind': 'variable', 'column': '人物'},
      {'kind': 'choice', 'column': '表情', 'options': ['happy', 'sad'],
       'multi': True},
      {'kind': 'optional', 'text': 'at', 'column': '位置'}],
     'lan happy sad at left',
     {'人物': 'lan', '表情': 'happy sad', '位置': 'at left'}),
    # 多值格**可以一个都不吃**，不许抢后面那格的词（甲：先到先得）
    ([{'kind': 'variable', 'column': '人物'},
      {'kind': 'choice', 'column': '表情', 'options': ['happy', 'sad'],
       'multi': True}],
     'happy',
     {'人物': 'happy'}),
    # 多值格后面还有**会吃词**的格子 → 它要让位（甲，这条以前两侧不一致）
    ([{'kind': 'variable', 'column': '人物'},
      {'kind': 'choice', 'column': '服装', 'options': ['happy', 'sad'],
       'multi': True},
      {'kind': 'choice', 'column': '神态', 'options': ['happy', 'sad']}],
     'lan happy sad',
     {'人物': 'lan', '服装': 'happy', '神态': 'sad'}),
    # 固定关键字格也吃词、也占位（否则整排串位）
    ([{'kind': 'variable', 'column': '人物'},
      {'kind': 'keyword', 'text': 'swim_suit'},
      {'kind': 'choice', 'column': '神态', 'options': ['happy']}],
     'lan swim_suit happy',
     {'人物': 'lan', '神态': 'happy'}),
]


def _trial_cells(pattern, line) -> dict:
    """试切那一侧：按列名收格子值（关键字格的空列名不算）。"""
    return {c['column']: c['text'] for c in P.match_line(pattern, line)['cells']
            if c['column'] and str(c['text']).strip()}


def _read_cells(pattern, line, want_keys) -> dict:
    """读值那一侧：只取**确实对应格子列名**的那些键。

    ⚠ `slot_value_map` 会额外挂"关键字原文"这类键（`{'show': 'show'}`），
    那是给 `slot_span` 兜底查区间用的，不是格子值 —— 这里按列名过滤。
    """
    got = SA.slot_value_map(line, pattern)
    return {k: v for k, v in got.items() if k in want_keys}


def test_试切与读值在策略不冲突的形状上逐格相同():
    for pattern, line, want in AGREE_CASES:
        trial = _trial_cells(pattern, line)
        assert trial == want, (line, trial, want)
        assert _read_cells(pattern, line, set(want)) == want, (
            line, SA.slot_value_map(line, pattern), want)


def test_分岔一_枚举格的候选跳过_而且写在_policy_上():
    """试切按书写顺序，读值把候选当词典 —— **同一分配器、两个 policy**。"""
    pattern = [{'kind': 'variable', 'column': '角色'},
               {'kind': 'choice', 'column': '服装', 'options': ['泳装', '校服']}]
    line = '小明 龙袍 泳装'
    trial = _trial_cells(pattern, line)
    assert trial == {'角色': '小明', '服装': '龙袍'}, trial
    assert P.match_line(pattern, line)['ok'] is False     # `泳装` 没人认领
    assert _read_cells(pattern, line, {'角色', '服装'}) == {'角色': '小明',
                                                          '服装': '泳装'}
    tokens = line.split()
    assert P.assign_tokens(tokens, pattern,
                           policy='order-first')['spans'] == {0: (0, 1),
                                                              1: (1, 2)}
    assert P.assign_tokens(tokens, pattern,
                           policy='candidates')['spans'] == {0: (0, 1),
                                                             1: (2, 3)}
    assert set(P.POLICIES) == {'order-first', 'candidates'}


def test_分岔二_引号只在读值那一侧去掉():
    """试切报的是**切出来的原文**（带引号），读值给的是**编剧改的值**（去引号）。"""
    pattern = [{'kind': 'variable', 'column': '人物'},
               {'kind': 'variable', 'column': '内容'}]
    line = 'lyj "泳装"'
    assert _trial_cells(pattern, line) == {'人物': 'lyj', '内容': '"泳装"'}
    assert _read_cells(pattern, line, {'人物', '内容'}) == {'人物': 'lyj',
                                                          '内容': '泳装'}


def test_读值这一侧确实是转发到同一个分配器():
    """`_claim` 不许再有自己的规则 —— 它必须逐字等于分配器的 `candidates` 结果。"""
    cases = [(p, ln) for p, ln, _w in AGREE_CASES]
    cases.append(([{'kind': 'variable', 'column': '角色'},
                   {'kind': 'choice', 'column': '服装',
                    'options': ['泳装', '校服']}], '小明 龙袍 泳装'))
    for pattern, line in cases:
        slots = P.parse_pattern(pattern)
        tokens = line.split()
        want = {i: ' '.join(tokens[a:b]) for i, (a, b)
                in P.assign_tokens(tokens, slots,
                                   policy='candidates')['spans'].items()}
        assert SA._claim(line, slots) == want, (line, SA._claim(line, slots),
                                               want)


def test_分配器的失败信号是报告而不是猜():
    slots = P.parse_pattern([{'kind': 'keyword', 'text': 'show'},
                             {'kind': 'variable', 'column': '人物'}])
    # ① 关键字对不上 → bad_kw（硬失败，不是"没词了"）
    got = P.assign_tokens(['lan'], slots)
    assert got['bad_kw'] == 0
    # ② 勾了限死却一个候选都没给 → rejected（与 `problems_of` 同口径）
    locked = P.parse_pattern([{'kind': 'choice', 'column': '人物',
                               'limit': True}])
    assert P.assign_tokens(['任何人都行'], locked)['rejected'] == 0
    # ③ 该吃词却没词了 → unfilled（"少了"与"多了"是两回事）
    got3 = P.assign_tokens(['show'], slots)
    assert got3['unfilled'] == [1] and got3['bad_kw'] is None
    # ④ 多出来的词照实报（不吞）
    got4 = P.assign_tokens(['show', 'lan', '啦啦'], slots)
    assert got4['used'] == 2 and not got4['unfilled']
    # 四种信号在 `match_line` 上都变成人话
    assert P.match_line([{'kind': 'keyword', 'text': 'show'}], 'x')['reason']
    assert '只能从这些里选' in P.match_line(
        [{'kind': 'choice', 'column': '人物', 'limit': True}],
        '任何人都行')['reason']
    assert '没人认领' in P.match_line(
        [{'kind': 'variable', 'column': '人物'}], 'a b')['reason']
