# -*- coding: utf-8 -*-
"""
切行 = **分词 + 定向路由**（2026-10-17 重构，用户现场）
================================================================================

用户粘了一行真剧本、界面切成这样：

    切出来是这样：lan base_swimsuit（角色） ｜ （服装） ｜ happy（表情）

他的诊断（原话）：

    "我注意到『角色』和『服装』都勾选了『可多个』。你的底层正则匹配在多选模式下
     极其贪婪（Greedy），它把 lan 和 base_swimsuit 当作了一个整体吃掉了……
     当相邻的两个参数都允许『多选』时，如果没有明确的分隔符（比如逗号），
     靠贪婪正则匹配必然会产生灾难性的越界抢占。"

    要求：彻底放弃贪婪捕获，改用分词器思路 —— `line.split()` 切成
    `["show", "lan", "base_swimsuit", "happy"]`，从左到右按"这个词在不在某一格的
    候选里"定向路由。`base_swimsuit` 不在角色的候选里，就绝对不会被角色格子吃掉。

根因（`pattern._can_allocate` 的旧口径）：多值格"一个都不吃"是合法解，于是
「服装」可以整格空着，"让位"给前一格把它的候选词当成自己的第二个值 ——
旧实现只问"后面每一格还能不能吃到词"，**从不问"这个词到底是谁的"**。

现在 `pattern._exact_spans` 做一次全局最优分配：每个词先查归属
（自己的候选 / 谁也不认识 / **别的格子的候选**），总代价最小的切法赢。
本文件钉住的就是"单词不会被越界抢占、也不会被拼接成别的串"。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.authoring import pattern as P                        # noqa: E402

ROLES = ['aaa', 'aabb', 'ay', 'ba', 'bbb', 'bd', 'dhy', 'dhyab', 'dororo',
         'fw', 'hidden_name', 'jc', 'jcab', 'jg', 'lan', 'ld', 'ling', 'ly',
         'mm', 'narrator', 'q', 'toto', 'txf', 'u', 'xing', 'xy', 'ye']
OUTFIT = ['clothes2', 'base_hand_up', 'base_swimsuit', 'base_arms_akimbo',
          'base_hands_down', 'hanfu_base', 'base_handdown']
FACE = ['afraid', 'angry', 'happy', 'sad', 'shy', 'smile', 'wink']

#: 用户现场的三格：**角色与服装都勾了"这一格可以有多个"**（相邻、都多值）。
THREE = [
    {'kind': 'variable', 'column': '角色', 'options': ROLES, 'multi': True},
    {'kind': 'choice', 'column': '服装', 'options': OUTFIT, 'multi': True},
    {'kind': 'choice', 'column': '表情', 'options': FACE},
]


def _cells(pattern, line):
    return [(c['column'], c['text']) for c in P.match_line(pattern, line)['cells']]


def _spans(pattern, line):
    slots = P.parse_pattern(pattern)
    return P.assign_tokens(line.split(), slots)


# ===========================================================================
#  ① 现场那一行：三格各归各位
# ===========================================================================

def test_the_user_line_cuts_into_three_independent_values():
    """`show lan base_swimsuit happy` → 角色=lan / 服装=base_swimsuit / 表情=happy。"""
    got = P.match_line([{'kind': 'keyword', 'text': 'show'}] + THREE,
                       'show lan base_swimsuit happy')
    assert got['ok'] is True, got
    assert _cells([{'kind': 'keyword', 'text': 'show'}] + THREE,
                  'show lan base_swimsuit happy')[1:] == [
        ('角色', 'lan'), ('服装', 'base_swimsuit'), ('表情', 'happy')], got


def test_三个格子的值互不包含_每个格子只吃自己那一两个词():
    """把"每格吃了哪几个词"摊开看：**区间不许重叠、不许跨格**。"""
    got = _spans([{'kind': 'keyword', 'text': 'show'}] + THREE,
                 'show lan base_swimsuit happy')
    assert got['exact'] is True, got
    spans = got['spans']
    assert spans == {0: (0, 1), 1: (1, 2), 2: (2, 3), 3: (3, 4)}, spans
    # 每个值都是**原样的词**（不拼接、不改写、不插下划线）
    tokens = 'show lan base_swimsuit happy'.split()
    for si, (a, b) in spans.items():
        assert ' '.join(tokens[a:b]) == tokens[a], (si, tokens[a:b])


def test_相邻两个多值格不许互相抢词():
    """
    用户点名的形状：**连续两格都勾了"可多个"**。

    `服装` 的候选词决不能被 `角色` 当成自己的第二个值 ——
    旧代码在这里给出 `lan base_swimsuit`（一个格子里两个词，还显示成一串）。
    """
    line = 'show lan base_swimsuit happy'
    cells = _cells([{'kind': 'keyword', 'text': 'show'}] + THREE, line)
    role, outfit = cells[1][1], cells[2][1]
    assert role == 'lan', cells
    assert outfit == 'base_swimsuit', cells
    assert 'base_swimsuit' not in role, cells


def test_多值格该吃多个的时候照样吃():
    """
    **没有**把"可多个"关掉：连着两个都属于服装的词仍然进同一格。

    ⚠ 2026-10-17 改（这是"垃圾词"那条判据的必然结果）：
      原来这里用的是 `clothes2 happy`，而 `happy` 是**表情**的候选 —— 多值格
      把它吃进来，正是用户投诉的"焊接邻格候选"。现在多值格只吃**自己人**，
      所以要证明"可多个"还活着，得用两个真属于服装的词（`clothes2 hanfu_base`）。
    """
    cells = _cells([{'kind': 'keyword', 'text': 'show'}] + THREE,
                   'show lan clothes2 hanfu_base happy')
    assert cells[2] == ('服装', 'clothes2 hanfu_base'), cells
    assert cells[3] == ('表情', 'happy'), cells


def test_多值格仍然可以一个都不吃():
    """`show lan happy`：服装没有属于自己的词 → 空着，不许抢 `happy`。"""
    cells = _cells([{'kind': 'keyword', 'text': 'show'}] + THREE,
                   'show lan happy')
    assert cells[1:] == [('角色', 'lan'), ('服装', ''), ('表情', 'happy')], cells


# ===========================================================================
#  ② 路由靠的是"候选归属"，不是位置
# ===========================================================================

@pytest.mark.parametrize('line, want', [
    ('lan base_swimsuit happy', [('角色', 'lan'), ('服装', 'base_swimsuit'),
                                 ('表情', 'happy')]),
    ('ye hanfu_base sad', [('角色', 'ye'), ('服装', 'hanfu_base'),
                           ('表情', 'sad')]),
    ('mm base_hands_down wink', [('角色', 'mm'),
                                 ('服装', 'base_hands_down'),
                                 ('表情', 'wink')]),
])
def test_每个词先看属于谁(line, want):
    assert _cells(THREE, line) == want


def test_角色一格没有候选也照样不越界():
    """
    用户那句"`lan` 这个变量在程序里不是这么定义的"的另一半：

    「角色」是**任意变量**格（候选只是下拉里的备选，甚至是空的）；
    没有候选就更要靠"别的格子认不认识这个词"来路由，而不是靠位置。
    """
    open_role = dict(THREE[0])
    open_role.pop('options')
    pattern = [open_role, THREE[1], THREE[2]]
    assert _cells(pattern, 'lan base_swimsuit happy') == [
        ('角色', 'lan'), ('服装', 'base_swimsuit'), ('表情', 'happy')]


def test_两个格子的候选重叠时按书写顺序各吃一个():
    """同名的词两边都有（`happy` 既是服装也是表情）→ 前一个格子先吃，不叠罗汉。"""
    pattern = [{'kind': 'choice', 'column': '服装', 'options': ['happy', 'sad'],
                'multi': True},
               {'kind': 'choice', 'column': '表情', 'options': ['happy', 'sad'],
                'multi': True}]
    assert _cells(pattern, 'happy sad') == [('服装', 'happy'), ('表情', 'sad')]


# ===========================================================================
#  ③ 分词：空格就是分隔符，候选本身可以含空格
# ===========================================================================

def test_多个空格和制表符都只是分隔符():
    got = P.match_line(THREE, 'lan   base_swimsuit\thappy')
    assert got['ok'] is True, got
    assert [(c['column'], c['text']) for c in got['cells']] == [
        ('角色', 'lan'), ('服装', 'base_swimsuit'), ('表情', 'happy')]


def test_候选本身含空格时整段认作一个值():
    """`base hands down` 这种候选（真机立绘就有）不能被拆成三个词再拼回去。"""
    pattern = [{'kind': 'choice', 'column': '服装',
                'options': ['base hands down', 'base_hand_up']}]
    got = P.match_line(pattern, 'base hands down')
    assert got['ok'] is True, got
    assert got['cells'][0]['text'] == 'base hands down', got['cells']


def test_没有空格的中文行仍然按候选切():
    """中文常态（`show小明泳装开心`）：没有空格 → 只能靠候选值切。"""
    pattern = [{'kind': 'variable', 'column': '角色', 'options': ['小明']},
               {'kind': 'choice', 'column': '服装', 'options': ['泳装', '校服']},
               {'kind': 'choice', 'column': '表情', 'options': ['开心', '害羞']}]
    assert _cells(pattern, '小明泳装开心') == [('角色', '小明'), ('服装', '泳装'),
                                              ('表情', '开心')]


def test_切不上时仍然如实报没人认领的词():
    """重构没有把"切不上"变成"假成功"：多出来的词照实报。"""
    locked = [THREE[0], THREE[1], dict(THREE[2], limit=True)]
    got = P.match_line(locked, 'lan base_swimsuit happy 多余的词')
    assert got['ok'] is False, got
    assert '没人认领' in got['reason'], got['reason']
    # ⚠ 失败那一屏也不许再摆出"一格吃了两个词"（那是另一个 bug 的观感）
    assert [c['text'] for c in got['cells']][:2] == ['lan', 'base_swimsuit'], got


def test_少一个词时格子照样各归各位():
    """`lan`（缺服装）：报"第 2 格该有一个词"，但第 1 格不许顺手把它也吃了。"""
    pattern = [{'kind': 'variable', 'column': '角色', 'options': ROLES},
               {'kind': 'choice', 'column': '服装', 'options': OUTFIT}]
    got = P.match_line(pattern, 'lan')
    assert got['ok'] is False, got
    assert [c['text'] for c in got['cells']] == ['lan', ''], got
    assert '第 2 格' in got['reason'], got['reason']


# ===========================================================================
#  ④ 真界面：试切那一栏显示三格、每格一个词
# ===========================================================================

def test_三格试切在界面上显示三个独立的内容(qapp, tmp_path):
    """
    走**真对话框**（用户看到的那一份），不走纯函数 —— 这是他那张截图的复现。

    ⚠ 对话框会摘掉行首的动作名（`show`），因为格子里描述的是"动作名后面"的东西。
    """
    from storygraph.story import statement_registry as SR
    from storygraph.ui_qt.dialog_learn import CustomStatementDialog

    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    (root / 'game' / 'ch.rpy').write_text('label start:\n    return\n',
                                          encoding='utf-8')
    SR.clear_cache()
    dlg = CustomStatementDialog(None, project_root=root, discovered=[],
                                registry_report='')
    try:
        dlg.name.setText('show')
        dlg.params.clear()
        role = dlg.params.add_param('角色', '角色', ', '.join(ROLES),
                                    kind='variable')
        role._multi.setChecked(True)
        outfit = dlg.params.add_param('服装', '服装', ', '.join(OUTFIT),
                                      kind='choice')
        outfit._multi.setChecked(True)
        dlg.params.add_param('表情', '表情', ', '.join(FACE), kind='choice')
        dlg.sample.setText('show lan base_swimsuit happy')
        dlg._refresh_cut()
        text = dlg.cut_view.text()
    finally:
        dlg.close()
        SR.clear_cache()
    assert 'lan（角色）' in text, text
    assert 'base_swimsuit（服装）' in text, text
    assert 'happy（表情）' in text, text
    assert '共 3 格' in text, text
    # 修复前的那一行长这样：`lan base_swimsuit（角色） ｜ （服装） ｜ happy（表情）`
    assert 'lan base_swimsuit（角色）' not in text, text
