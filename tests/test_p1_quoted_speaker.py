"""
P1-C 回归 · 引号形式的说话人不许被**静默**判成旁白
=====================================================

审计（`docs/P0审计_不安全因素_第三轮_2026-10-25.md` §2.2）复测的 6 个用例：
`MOM"…"` / `小雨"…"` / `Mom"…"`（角色表空与非空）+ 两个对照。其中 4 个原来
**静默旁白化** —— 台词失去说话人，`warnings` / `unmapped` / `suspect_speakers`
全空，用户回游戏才发现全员旁白。

修法的**边界**（本文件的重点）：`_suspect_speakers` 的网扩到引号形状，但
**判读一个字都不改** —— `小雨"台词"` 仍然是旁白，只是多一条提案
（`suspect_speakers` 里的一行）+ 一条人话警告。绝不能因为"看着像"就自动认成
角色：`他说"你好"` 是真旁白，误判它就是**静默改剧情**。

⚠ C-5 的口径：审计表里那一行写的是"空角色表 + `MOM"台词"`"，但空表 + 全大写
**就是 C-1**（全大写缩写那条判据要求角色表非空，`_looks_like_speaker` 自己
写着）。审计探针 `tools/_p0_audit_round3_probe.py` 实际给 C-5 传的是**非空**
角色表 —— 于是它被认成台词、走"未映射提案 + 未定义变量警告"那条老网。本文件
按探针的真实口径钉 C-5（也只能这样，"保持现状不重复"才有意义）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.authoring.txt_import import (  # noqa: E402
    CharacterMap, convert_text)

#: 与审计探针同一份角色表：**有一个不相干的角色**，没有 MOM / 小雨 / Mom。
#: （C-3 的"角色表里有妈妈"用 `mm`/「妈妈」表达 —— 变量名不是 mom，所以
#: `Mom` 归一后仍然查不到；若把 `MOM` 写进别名列，它就会合法命中、不再是缺陷。）
ROWS = {'lj': {'ID': 'lj', 'Name': '林静'},
        'mm': {'ID': 'mm', 'Name': '妈妈'}}

#: C-1..C-4：引号形状、名字认不出 → 必须"提案 + 警告"，不许静默旁白化
QUOTED_FAILING_CASES = [
    ('C-1 空表 + 全大写缩写', {}, 'MOM"你回来了。"'),
    ('C-2 空表 + 中文名', {}, '小雨"你回来了。"'),
    ('C-3 非空表 + 混合大小写', ROWS, 'Mom"台词"'),
    ('C-4 非空表 + 中文名', ROWS, '小雨"台词"'),
]

#: 真旁白（反例）：这些**一行都不许**进 `suspect_speakers`
REAL_NARRATION = [
    '他说"你好"',                       # 中文"说"字：名字闸门挡掉
    '她说"我不知道。"',
    '小明笑着说"走吧"',
    '我"不知道。"',                      # 单汉字不是名字边界（2–4 汉字才算）
    '他"你好"',
    '"独立的一句旁白"',                  # 行首就是引号 → 没有"引号前缀"
    '世界上再也没有什么比"自由"更重要',   # 长前缀 + 闭引号后还有正文
    '突然"砰"的一声',                    # 同：闭引号不是行尾
]


def _convert(line, rows=None):
    """单行 → `ConversionResult`（不写盘、不读配置）。"""
    return convert_text(line + '\n', characters=CharacterMap(rows or {}),
                        filename='t.txt', label='x')


def _kinds(res):
    return {line.raw: line.kind for line in res.lines}


def _has_speaker_question(res):
    """回执里必须有一句**问句**："要不要把它当成角色？"（提案的人话形态）。"""
    return any('看着像谁在说话' in w and '要不要把它当成角色' in w
               for w in res.warnings)


# ============ 审计 6 用例 ============

@pytest.mark.parametrize('case, rows, line', QUOTED_FAILING_CASES,
                         ids=[c[0] for c in QUOTED_FAILING_CASES])
def test_quoted_speaker_is_proposed_instead_of_silently_narrated(case, rows,
                                                                 line):
    """
    ⭐ C-1..C-4（审计里 4 个 FAIL）：`名字"台词"` 认不出名字时，**必须**
    在 `suspect_speakers` 里提案、并进 `warnings` 的人话摘要。

    ⚠ 同时钉住"只报不猜"：这一行**仍然是旁白**（判读不改）、`unmapped` 为空
    （不自动建角色/不自动 define）—— 提案是让人确认，不是让我们猜。
    """
    res = _convert(line, rows)
    token = line.split('"')[0]

    assert [s['token'] for s in res.suspect_speakers] == [token], \
        (case, res.suspect_speakers)
    assert res.suspect_speakers[0]['no'] == 1
    assert res.suspect_speakers[0]['shape'] == 'quote', res.suspect_speakers
    assert _has_speaker_question(res), (case, res.warnings)
    assert any(token in w for w in res.warnings if '看着像谁在说话' in w), \
        res.warnings

    # 只报不猜：判读没被改动
    assert _kinds(res)[line] == 'narration', (case, _kinds(res))
    assert res.unmapped == {}, (case, res.unmapped)


def test_c5_all_caps_keeps_the_unmapped_net_without_duplicate_report():
    """
    C-5 对照：`MOM"台词"` + **非空**角色表 —— 全大写缩写本来就会被认成台词
    （`_looks_like_speaker` 第 2 条），走"未映射提案 + 未定义变量警告"那条老网。

    补网之后**不许重复**：它已经是 `say`，不该再进 `suspect_speakers`，
    也不该多出"看着像谁在说话"那句。
    """
    res = _convert('MOM"台词"', ROWS)

    assert _kinds(res)['MOM"台词"'] == 'say', _kinds(res)
    assert res.unmapped == {'MOM': 1}, res.unmapped
    assert any('不在角色表里' in w for w in res.warnings), res.warnings
    assert any('未定义变量' in w for w in res.warnings), res.warnings

    assert res.suspect_speakers == [], res.suspect_speakers
    assert not _has_speaker_question(res), res.warnings
    assert sum('看着像谁在说话' in w for w in res.warnings) == 0, res.warnings


def test_c6_colon_form_keeps_its_net_and_is_not_reported_twice():
    """
    C-6 对照：冒号形状的网（2026-10-11）保持现状 —— `小雨：台词` 照旧是旁白、
    照旧提案一次，**不会**因为扩了引号网而重复一条警告。
    """
    res = _convert('小雨：台词', ROWS)

    assert [s['token'] for s in res.suspect_speakers] == ['小雨'], \
        res.suspect_speakers
    assert res.suspect_speakers[0]['shape'] == 'colon', res.suspect_speakers
    assert _kinds(res)['小雨：台词'] == 'narration', _kinds(res)
    assert sum('看着像谁在说话' in w for w in res.warnings) == 1, res.warnings
    # 冒号形状没有引号，不该被加上"引号形式"那句
    assert not any('引号形式' in w for w in res.warnings), res.warnings


# ============ 反例：真旁白不许被误提案 ============

@pytest.mark.parametrize('line', REAL_NARRATION)
def test_real_narration_is_not_proposed(line):
    """
    ⭐ "不要擅自把所有引号前缀都当说话人"：中文旁白里引号到处都是，
    `他说"你好"` / `"独立的一句旁白"` 这类**一行都不许**进 suspect。
    """
    res = _convert(line, ROWS)
    assert res.suspect_speakers == [], (line, res.suspect_speakers)
    assert not _has_speaker_question(res), (line, res.warnings)


def test_a_recognized_speaker_with_quotes_is_not_flagged():
    """
    认得出的引号说话人（别名列里写了 `MOM`）→ 台词、映射到 `mm`，
    既不该进 suspect，也不该报未映射（**别乱报是同一枚硬币的另一面**）。
    """
    chars = {'mm': {'ID': 'mm', 'Name': '妈妈', 'Aliases': 'MOM|母亲'}}
    res = _convert('Mom"你回来了。"', chars)

    assert _kinds(res)['Mom"你回来了。"'] == 'say', _kinds(res)
    assert res.unmapped == {}, res.unmapped
    assert res.suspect_speakers == [], res.suspect_speakers
    assert '    mm "你回来了。"' in res.rpy_text, res.rpy_text


# ============ 形状细节 ============

def test_fullwidth_quotes_are_the_same_shape():
    """中文引号 `“…”` 与英文 `"…"` 同形状 —— 编剧两种都会写。"""
    res = _convert('小雨“你回来了。”', ROWS)
    assert [s['token'] for s in res.suspect_speakers] == ['小雨'], \
        res.suspect_speakers
    assert _has_speaker_question(res), res.warnings


def test_stage_direction_after_the_quote_does_not_hide_the_speaker():
    """括号注释在引号后面（`（笑）`）时，先剥注释再判形状 —— 别因此漏报。"""
    res = _convert('小雨"你回来了。"（笑）', ROWS)
    assert [s['token'] for s in res.suspect_speakers] == ['小雨'], \
        res.suspect_speakers
    assert _has_speaker_question(res), res.warnings


def test_unclosed_quote_is_a_documented_non_goal():
    """
    已知边界（**故意不做**）：引号没闭合的 `小雨"你回来了`。

    真机 5 章 1372 行里这条形状唯一的命中是作者贴进正文的报错栈
    `File "C:\\admin_script.py", line 24, in <module>` —— 扩到它会把一行
    英文报错当成说话人，噪声会让人无视整条警告（比不报更糟）。
    """
    res = _convert('小雨"你回来了', ROWS)
    assert res.suspect_speakers == [], res.suspect_speakers
    assert not _has_speaker_question(res), res.warnings


def test_quote_followed_by_more_narration_is_a_documented_non_goal():
    """
    已知边界（**故意不做**）：闭引号后面还有正文的 `小雨"台词"然后走了`。

    真台词的行尾就是闭引号（括号注释不算正文，已先剥掉）。把"闭引号后还有
    正文"也算进来，等于把"句子里带引号"的旁白整片拉进提案 —— 提案一多就会被
    人无视，那比少报更糟。所以这条形状**宁可漏**。
    """
    res = _convert('小雨"台词"然后走了', ROWS)
    assert res.suspect_speakers == [], res.suspect_speakers
    assert not _has_speaker_question(res), res.warnings
