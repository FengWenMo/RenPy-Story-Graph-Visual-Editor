# -*- coding: utf-8 -*-
"""
「这一格可填可不填」—— 用户 2026-10-18 现场（真实项目 `sr_general`）
================================================================================

用户原话：

    > 默认写法会报告切不上……实际上服装那一栏可填可不填……
    > 不填就是默认写法，你可以去真实项目内查看一下定义。

去项目里核对过了（`game/definitions.rpy` 的 `layeredimage lan` + 真实剧本）：

    show ye happy                  出现 11 次   ← 没写服装
    show lan happy                 出现  5 次   ← 没写服装
    show ling at bounce_once:      出现  1 次   ← 一个属性都没有

Ren'Py 的 `show` 是 `show <tag> [属性…]`，**属性本来就是可选的**。
编辑器把 `show ye base_handdown happy` 拆成「角色｜服装｜表情」三格，
于是"没写服装"看起来像"少了一个词"，试切就报

    ⚠ 切不上：这些格子没吃到东西：服装；第 2 格（服装）在第 3 个词的位置上
      该有一个词，但这一行里没有（这一行共 2 个词）

—— 对着一条**完全合法**的行让人改半天。这就是这一轮要修的东西。

修法两半（都不动代价表里任何一个数字）：

  · `pattern.parse_pattern` 认 `required`（缺省 True，老声明一个字不变）；
  · `_spans` 里「一个都不吃」：`multi` 或 `required=False` → 免费；
  · `_report` 同理：这两种格子空着**不算** `unfilled`；
  · `pattern.with_optional_defaults(slots, head)`：`show/scene/hide` 的
    **属性格**（第一个取值格之后的取值格）默认 `required=False` ——
    第一个取值格是立绘 tag，没有它这一行不成立。

⚠ 不用 `xfail`：绿=现在对，红=还没修。

跑法::

    python -m pytest tests/test_cut_optional_slots.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.authoring import pattern as P                        # noqa: E402

SHOW = {'kind': 'keyword', 'text': 'show'}


def build(**flags):
    """`show` 的三格：角色｜服装｜表情（`flags` 用来改某一格的 `required`）。"""
    return [
        SHOW,
        {'kind': 'variable', 'column': '角色', 'options': ['lan'],
         **flags.get('role', {})},
        {'kind': 'choice', 'column': '服装', 'options': ['base_swimsuit'],
         **flags.get('outfit', {})},
        {'kind': 'choice', 'column': '表情', 'options': ['happy'],
         **flags.get('face', {})},
    ]


def _run(pattern, line):
    slots = P.parse_pattern(pattern)
    return P.assign_tokens(P.tokenize(line, slots), slots)


# ===========================================================================
#  一、`required=False` 真的让"空着"合法（不动代价表）
# ===========================================================================

def test_默认写法_show_lan_happy_在服装必填时确实报缺词():
    """
    参照系：**不改口径**时它就是红的 —— 说明下面那条绿不是"本来就绿"。
    """
    got = _run(build(), 'show lan happy')
    assert got['exact'] is False, got
    assert got['unfilled'] == [2], got['unfilled']
    assert got['values'].get(1) == 'lan', got['values']
    assert got['values'].get(3) == 'happy', got['values']


def test_服装标成可不填之后_show_lan_happy_就切上了():
    """
    用户要的那条：`show lan happy` = 角色 lan、服装空（用默认）、表情 happy。
    """
    got = _run(build(outfit={'required': False}), 'show lan happy')
    assert got['exact'] is True, got
    assert got['unfilled'] == [], got['unfilled']
    assert got['values'].get(1) == 'lan', got['values']
    assert got['values'].get(2, '') == '', got['values']
    assert got['values'].get(3) == 'happy', got['values']
    assert got['leftover'] == [], got['leftover']


def test_可不填的格子不许因此抢别人的词():
    """
    护栏：`required=False` 只让"空着"免费，**不许**变成"什么都吃得下"。

    `show lan happy` 里 `happy` 是表情的候选：
    服装（可不填）如果把它吃走，表情就空了 —— 那是假成功。
    """
    got = _run(build(outfit={'required': False}), 'show lan happy')
    assert got['values'].get(2, '') == '', got['values']
    assert got['values'].get(3) == 'happy', got['values']


def test_必填的那一格缺了还是要报():
    """护栏：不勾"可不填"的格子，缺词照旧点名（别把整行都放过）。"""
    got = _run(build(outfit={'required': False}), 'show lan')
    assert got['exact'] is False, got
    assert got['unfilled'] == [3], got['unfilled']


# ===========================================================================
#  二、默认从哪来：`show/scene/hide` 的属性格（`with_optional_defaults`）
# ===========================================================================

def test_show_的属性格默认可不填_tag_那一格仍然必填():
    slots = P.with_optional_defaults(build(), 'show')
    assert slots[0].get('required') is None, slots[0]      # 固定字不动
    assert slots[1].get('required') is None, slots[1]      # 角色 = tag，必填
    assert slots[2].get('required') is False, slots[2]     # 服装
    assert slots[3].get('required') is False, slots[3]     # 表情


@pytest.mark.parametrize('head', ['show', 'scene', 'hide'])
def test_三个立绘语句都按这条来(head):
    slots = P.with_optional_defaults(build(), head)
    assert [s.get('required') for s in slots[1:]] == [None, False, False], slots


@pytest.mark.parametrize('head', ['mycue', 'play', '', None])
def test_别的语句一个字都不改(head):
    """自定义语句的参数**可能是必填的** —— 不许顺手放过。"""
    slots = P.with_optional_defaults(build(), head)
    assert all('required' not in s for s in slots), slots


def test_人明确说了必填就不许被默认覆盖():
    """界面勾掉"可以不填" = 明确的 `required: True` —— 默认只补缺。"""
    slots = P.with_optional_defaults(
        build(outfit={'required': True}), 'show')
    assert slots[2].get('required') is True, slots[2]
    assert slots[3].get('required') is False, slots[3]


def test_默认之后_show_lan_一个属性都不写也能切上():
    """真实项目里有 `show ling at bounce_once:` —— 属性整段省略是合法的。"""
    slots = P.parse_pattern(P.with_optional_defaults(build(), 'show'))
    got = P.assign_tokens(P.tokenize('show lan', slots), slots)
    assert got['exact'] is True, got
    assert got['values'].get(1) == 'lan', got['values']


def test_默认之后_写了服装照旧认():
    got = _run(P.with_optional_defaults(build(), 'show'),
               'show lan base_swimsuit happy')
    assert got['exact'] is True, got
    assert got['values'].get(2) == 'base_swimsuit', got['values']
    assert got['values'].get(3) == 'happy', got['values']


# ===========================================================================
#  三、与空引号那条规矩不打架
# ===========================================================================

def test_空引号仍然按位置废掉那一格_哪怕它可不填():
    """
    `required=False` 不会让空引号"被跳过"：`""` 仍然是**位置**的主人，
    轮到服装就仍然是服装死（不是表情、也不是"无所谓"）。
    """
    got = _run(P.with_optional_defaults(build(), 'show'),
               'show lan "" base_swimsuit happy')
    assert got['broken'] == [2], got['broken']
    assert got['values'].get(2) == '', got['values']
    assert got['exact'] is False, '空引号是输入错误，永远不算切上'


# ===========================================================================
#  四、界面：勾选框 / 往返 / 入口
# ===========================================================================

def test_卡片上没动过勾选框就不写required_动过才写(qapp):
    """
    **"没说"和"说了 False"是两件事**：

      · 没动过 → `data()` 里没有 `required` 键（由语句头决定默认）；
      · 勾上"可以不填" → `required: False`；
      · 再取消 → `required: True`（明确说必填，默认不许再覆盖）。
    """
    from storygraph.ui_qt.param_editor import FieldCard

    card = FieldCard()
    card._kind.setCurrentIndex(card._kind.findData('choice'))
    card._name.setText('服装')
    assert 'required' not in card.data(), card.data()

    card._optional.setChecked(True)
    assert card.data().get('required') is False, card.data()

    card._optional.setChecked(False)
    assert card.data().get('required') is True, card.data()

    # 往返：`set_data(required=False)` 要恢复成"勾着 + 动过"
    card2 = FieldCard()
    card2._kind.setCurrentIndex(card2._kind.findData('choice'))
    card2.set_data('', '服装', 'base_swimsuit', kind='choice',
                   required=False)
    assert card2._optional.isChecked() is True
    assert card2.data().get('required') is False, card2.data()


def test_弹窗里_show_的第二三格默认勾上可以不填_第一格不勾(qapp):
    from storygraph.ui_qt.dialog_learn import CustomStatementDialog

    dlg = CustomStatementDialog(None, project_root=None, discovered=[])
    dlg.params.clear()
    a = dlg.params.add_param('', '角色', 'lan', kind='variable')
    b = dlg.params.add_param('', '服装', 'base_swimsuit', kind='choice')
    c = dlg.params.add_param('', '表情', 'happy', kind='choice')
    dlg.params.set_optional_defaults(True)      # `show` 的那条默认

    assert a._optional.isChecked() is False, '第一个取值格是 tag，必须填'
    assert b._optional.isChecked() is True
    assert c._optional.isChecked() is True
    # ⚠ 默认不算"人动过"：`data()` 里仍然没有 `required`，
    #   真正的 `required: False` 由 `with_optional_defaults` 在写声明时补上。
    assert 'required' not in b.data(), b.data()


def test_试切面板_show_lan_happy_不再报切不上(qapp):
    """端到端：面板上那句话从"⚠ 切不上…"变成"切出来是这样…"。"""
    from storygraph.ui_qt.dialog_learn import CustomStatementDialog

    dlg = CustomStatementDialog(None, project_root=None, discovered=[])
    dlg.params.clear()
    dlg.name.setText('show')
    dlg.params.add_param('', '角色', 'lan', kind='variable')
    dlg.params.add_param('', '服装', 'base_swimsuit', kind='choice')
    dlg.params.add_param('', '表情', 'happy', kind='choice')
    dlg.sample.setText('show lan happy')
    dlg._refresh_cut()
    text = dlg.cut_view.text()
    assert '切不上' not in text, text
    assert '切出来是这样' in text, text
    assert 'lan（角色）' in text, text
    assert 'happy（表情）' in text, text
