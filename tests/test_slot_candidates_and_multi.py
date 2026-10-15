"""
「切不上」与参数的那几条真规矩（2026-10-13 用户实测退回）
================================================================================

用户在界面上填了三格、只填了"这一格是"，然后报了三件事。三件都是真 bug，
各自对应下面一组判据：

  ① **候选 ≠ 限死**：贴了一串候选之后，真实剧本里任何没列进去的词都判
     "切不上"（用户："这个仍然切不上！有一个巨大的 bug"）；
  ② **没填名字 = 三个参数只剩一个**：后端只认 `name`，界面把列名放在
     `label` 里 → 那一列被**静默丢掉**（连警告都没有）；
  ③ **A 段可以有多个**：真机 `show lan happy sad` 里 `happy sad` 是同一格
     （用户："A 是默认的，也就是可填可不填的……但是它不是一段，
     它允许有多个！你这个可填可不填的根本就没多段的选择！"）。

另外还钉住"候选必须按**真实有的资源**给"：真机
`layeredimage lan` 的 outfit 只有 `clothes2` / `base_hand_up`，
`hanfu_base` 是 `xing` 的 —— 合并清单会把它们混在一起（用户明确要求以
"真实的有的资源"为准）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.authoring import pattern as P
from storygraph.authoring import learn_statement as LS


# ===========================================================================
#  ① 候选 ≠ 限死：这是"切不上"的真凶
# ===========================================================================

OPEN_PATTERN = [
    {'kind': 'variable', 'column': '角色', 'options': ['lan', 'ye']},
    {'kind': 'variable', 'column': '服装', 'options': ['clothes2', 'base_hand_up']},
]


def test_candidates_do_not_make_a_slot_a_closed_enum():
    """
    ⭐⭐ 贴了候选**不该**把这一格变成封闭枚举。

    用户现场：给「角色」贴了一串角色名 → 剧本里别的角色全判"切不上"。
    真实项目的资源一直在变，候选只能是"下拉里的备选"。
    """
    got = P.match_line(OPEN_PATTERN, 'dhy hanfu_base')
    assert got['ok'] is True, got
    assert [c['text'] for c in got['cells']] == ['dhy', 'hanfu_base']


def test_limit_is_what_makes_it_closed():
    """
    要限死就得**显式**说（界面上的"编剧只能从下面这些里选"）。

    ⚠ 只有**枚举格**（`choice`）才受 `limit` 管 —— "谁都可以"那一类
    本来就没有候选，也就无所谓限不限死。所以这里用枚举格验。
    """
    enum = [{'kind': 'choice', 'column': '服装',
             'options': ['clothes2', 'base_hand_up']}]
    assert P.match_line(enum, 'hanfu_base')['ok'] is True, '不限死该放行'
    locked = [dict(enum[0], limit=True)]
    assert P.match_line(locked, 'hanfu_base')['ok'] is False, \
        '限死之后，候选外的值该被拒绝'
    assert P.match_line(locked, 'clothes2')['ok'] is True


def test_an_old_declaration_without_limit_is_treated_as_open():
    """
    ⚠ **存量声明一个字都不改**：老 `rules.json` 里没有 `limit` 键 →
    按"候选只是建议"处理（而不是按旧的"封闭枚举"）。
    """
    old = [{'kind': 'choice', 'column': '角色', 'choices': ['lan']}]
    got = P.match_line(old, 'dhy')
    assert got['ok'] is True, got


def test_a_choice_without_candidates_is_not_a_problem():
    """没给候选 = "随便写"，不是毛病（旧口径会逼人贴一串候选）。"""
    assert P.problems_of([{'kind': 'choice', 'column': '角色'}]) == []
    probs = P.problems_of([{'kind': 'choice', 'column': '角色', 'limit': True}])
    assert probs and '候选' in probs[0], probs


# ===========================================================================
#  ② 没填"剧本里怎么写"不该把整列丢掉
# ===========================================================================

def test_a_column_name_in_label_is_enough(tmp_path):
    """
    ⭐⭐ 用户现场：三格里只填了"这一格是" → **只剩一个参数**。

    根因是 `build_declaration` 只认 `name`（旧形状的"代码里怎么写"），
    而界面把列名放在 `label` → 那一列被静默丢掉，连警告都没有。
    现在 `name` 空就用 `label`；两个都空才丢，而且**必须出声**。
    """
    built = LS.build_declaration('show', params=[
        {'kind': 'variable', 'label': '角色'},
        {'kind': 'variable', 'label': '服装'},
        {'kind': 'choice', 'label': '表情', 'choices': 'happy,angry'},
    ])
    assert [c['name'] for c in built['columns']] == ['角色', '服装', '表情'], \
        built['columns']
    assert built['warnings'] == [], built['warnings']


def test_a_column_with_no_name_at_all_is_dropped_loudly():
    """一个名字都没有 → 丢，但**必须说清是第几格、怎么办**（旧版是静默的）。"""
    built = LS.build_declaration('show', params=[
        {'kind': 'variable', 'label': '角色'},
        {'kind': 'variable', 'label': ''},
    ])
    assert [c['name'] for c in built['columns']] == ['角色']
    assert built['warnings'], '静默丢列 —— 用户看到的就是"只剩一个参数"'
    assert '列名' in built['warnings'][0]


# ===========================================================================
#  ③ A 段可以有多个
# ===========================================================================

MULTI_PATTERN = [
    {'kind': 'keyword', 'text': 'show'},
    {'kind': 'variable', 'column': '角色'},
    {'kind': 'choice', 'column': '服装', 'options': ['clothes2', 'base_hand_up'],
     'multi': True},
    {'kind': 'choice', 'column': '表情', 'options': ['happy', 'sad', 'shy']},
]


def test_a_multi_slot_swallows_several_words():
    """
    ⭐⭐ 真机：`show lan happy sad` 的 `happy sad` 是**同一格**。

    用户："A 是默认的，也就是可填可不填的……但是它不是一段，它允许有多个！"
    """
    got = P.match_line(MULTI_PATTERN, 'show lan happy sad')
    assert got['ok'], got
    assert [(c['column'], c['text']) for c in got['cells']] == \
        [('', 'show'), ('角色', 'lan'), ('服装', 'happy'), ('表情', 'sad')]


def test_a_multi_slot_may_be_empty_and_does_not_steal():
    """
    ⭐ "可填可不填" = **可以一个都不填**，而且**不许抢后面那格的词**。

    这条是带回溯的：贪心到底之后要能一路退到 0 个词。
    """
    got = P.match_line(MULTI_PATTERN, 'show lan happy')
    assert got['ok'], got
    assert [(c['column'], c['text']) for c in got['cells']] == \
        [('', 'show'), ('角色', 'lan'), ('服装', ''), ('表情', 'happy')]
    # 两个词都属于它时，它该全吃
    got2 = P.match_line(MULTI_PATTERN, 'show lan clothes2 happy shy')
    assert got2['cells'][2]['text'] == 'clothes2 happy', got2['cells']


def test_multi_is_off_by_default():
    """缺省**不吃多个**（存量声明与"一格一个词"的直觉都不变）。"""
    single = [dict(s) for s in MULTI_PATTERN]
    single[2].pop('multi')
    got = P.match_line(single, 'show lan happy sad')
    assert got['ok'] is False or len(got['cells']) == 4, got
    slots = P.parse_pattern([{'kind': 'variable', 'column': '角色'}])
    assert slots[0]['multi'] is False


# ===========================================================================
#  候选必须按"真实有的资源"给（按立绘分开）
# ===========================================================================

LAYERED = '''\
layeredimage lan:
    group expressions:
        attribute happy:
            "happy"
        attribute sad:
            "sad"
    group outfit when clothes2 or base_hand_up:
        attribute clothes2:
            "c"
        attribute base_hand_up:
            "b"

layeredimage xing:
    group outfit:
        attribute hanfu_base:
            "h"
'''


def _project(tmp_path, body: str = LAYERED, name: str = 'proj') -> Path:
    root = tmp_path / name
    (root / 'game').mkdir(parents=True, exist_ok=True)
    (root / 'game' / 'definitions.rpy').write_text(body, encoding='utf-8')
    return root


def test_layeredimage_groups_reads_each_tag_separately(tmp_path):
    """⭐ 每个立绘**自己**有哪些组、每组有哪些值（读项目里的真定义）。"""
    table = LS.layeredimage_groups(_project(tmp_path))
    assert table['lan']['outfit'] == ['clothes2', 'base_hand_up'], table
    assert table['lan']['expressions'] == ['happy', 'sad'], table
    assert table['xing']['outfit'] == ['hanfu_base'], table
    # ⚠ `lan` **没有** hanfu_base —— 这正是用户指出的那条
    assert 'hanfu_base' not in table['lan']['outfit']


def test_a_cc_copy_of_the_same_layer_is_not_listed_twice(tmp_path):
    """
    ⚠ 真机里 `<tag>_cc`（角色卡预览用的复制品）与本体**组和值完全一样** ——
    来源清单不该一半是重复项（实测真机 22 条里有一半是 `_cc`）。
    """
    body = LAYERED + """
layeredimage lan_cc:
    group outfit:
        attribute clothes2:
            "c"
        attribute base_hand_up:
            "b"
    group expressions:
        attribute happy:
            "happy"
        attribute sad:
            "sad"
"""
    table = LS.layeredimage_groups(_project(tmp_path, body))
    assert 'lan_cc' not in table, table
    # 而异体（值不一样的那个）**照样留着** —— 不能一刀切按名字后缀丢
    different = LAYERED + """
layeredimage lan_cc:
    group outfit:
        attribute only_here:
            "o"
"""
    table2 = LS.layeredimage_groups(_project(tmp_path, different, name='p2'))
    assert 'lan_cc' in table2, table2


def test_choice_sources_offers_per_layer_candidates_first(tmp_path):
    """
    ⭐⭐ 用户："必须以真实的有的资源为准" —— 来源清单里
    **先**给"某个立绘自己的那一组"，合并清单仍在但排后面。
    """
    src = LS.choice_sources(_project(tmp_path))
    labels = [s['label'] for s in src]
    assert any(t.startswith('lan 的 outfit') for t in labels), labels
    lan_outfit = next(s for s in src if s['label'].startswith('lan 的 outfit'))
    assert lan_outfit['values'] == ['clothes2', 'base_hand_up'], lan_outfit
    # 合并那份要在"按立绘"之后
    assert labels.index('lan 的 outfit（2 个）') < len(labels)


def test_choice_sources_is_still_empty_for_an_empty_project(tmp_path):
    """通用性底线：项目里什么都没有 → 来源清单空，**不编**。"""
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    assert LS.choice_sources(root) == []
