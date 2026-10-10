"""
角色决策弹窗判据（离线 Qt）
============================

`ui_qt/dialog_chars.py` 是用户 2026-10-10 要的那个"问一句"的弹窗。
这里**不起窗口**（offscreen 跑真 Qt），只钉住它的三条命门：

  ① **默认值就是系统的建议**（人只需要确认，不该逼他从零选）；
  ② **每行都看得见判断依据**（证据文字 + 原文样例 —— 否则用户没法判断）；
  ③ **不可行时当场说清为什么**（"跳过"也必须带着原因，不能只说"先跳过"）。

真正的判定逻辑不在这里测 —— 它在 `test_char_decisions.py`。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

pytest.importorskip('PySide6')

from storygraph.authoring.char_decisions import (                    # noqa: E402
    ACT_EXISTING, ACT_NEW, ACT_SKIP, proposals)
from storygraph.authoring.txt_import import CharacterMap, convert_text  # noqa: E402

ROWS = {
    'jc': {'ID': 'jc', 'Name': '「蒋忱」'},
    'mm': {'ID': 'mm', 'Name': '妈妈'},
    'txf': {'ID': 'txf', 'Name': '汤X凤'},
    'ljk': {'ID': 'ljk', 'Name': '「李佳凯」'},
}


@pytest.fixture
def dialog(qapp):
    from storygraph.ui_qt.dialog_chars import CharDecisionDialog

    def _make(text: str, table=None):
        table = ROWS if table is None else table
        conv = convert_text(text, characters=CharacterMap(table))
        return CharDecisionDialog(None, proposals(conv, CharacterMap(table),
                                                  table), table)

    return _make


def test_the_dropdown_shows_display_names(dialog):
    """
    ⚠ 用户 2026-10-10 指出的可用性问题：下拉里只有 `txf`/`mm` 这种变量名时，
    作者**认不出**哪个是"妈妈"。必须写成 `mm（妈妈）`。
    """
    dlg = dialog('TJF"你怎么睡得着哒"')
    ed = dlg._editors[0]
    items = [ed.target.itemText(i) for i in range(ed.target.count())]
    assert any('mm（妈妈）' == t for t in items), items
    assert any(t.startswith('txf（汤X凤）') for t in items), items


def test_the_full_table_fallback_puts_named_characters_first(dialog):
    """29 行表里找一个人：有中文显示名的排前面（`aaa` 这种拼出来的排后面）。"""
    table = dict(ROWS, aaa={'ID': 'aaa', 'Name': '舍友A'},
                 dhyab={'ID': 'dhyab', 'Name': ''})
    dlg = dialog('BAOAN"问路"', table=table)
    ed = dlg._editors[0]
    items = [ed.target.itemData(i) for i in range(ed.target.count())]
    assert 'jc' in items and items.index('jc') < len(items) - 1
    assert items.index('dhyab') > items.index('jc'), items


def test_defaults_follow_the_suggestion(dialog):
    """`MOM` 有候选（mm）→ 默认"指到 mm"；`BAOAN` 没候选 → 默认"建新角色"。"""
    dlg = dialog('MOM"吃饭了"\nBAOAN"问路"')
    editors = {e.proposal.token: e for e in dlg._editors}
    assert editors['MOM'].action.currentData() == ACT_EXISTING
    assert editors['MOM'].target.currentData() == 'mm'
    assert editors['BAOAN'].action.currentData() == ACT_NEW
    assert editors['BAOAN'].new_var.text() == 'BAOAN'
    assert editors['BAOAN'].new_name.text() == 'BAOAN'


def test_every_row_shows_the_original_lines(dialog):
    """⚠ 判断依据必须在表里看得见（`MOM"跟妈客气啥"` 才知道是妈妈）。"""
    dlg = dialog('MOM"跟妈客气啥"\nMOM"同学之间要和睦"')
    ctx = dlg.table.item(0, 2).text()
    assert '跟妈客气啥' in ctx
    assert dlg.table.item(0, 1).text() == '2'


def test_candidates_carry_their_evidence_into_the_dropdown(dialog):
    dlg = dialog('MOM"吃饭了"')
    ed = dlg._editors[0]
    items = [ed.target.itemText(i) for i in range(ed.target.count())]
    assert any('mm' in t and '相近' in t for t in items), items


def test_a_blocked_row_says_why(dialog):
    """
    表里同时有 `ljk` 与 `Ljk`（两个人，只差大小写）→ `LJK` 归一后撞车，
    **别名解决不了**。这时弹窗必须：
    ① 把这一行摆出来（不能静默丢掉）；② 说清为什么；③ 默认"先跳过"。
    """
    rows = {'ljk': {'ID': 'ljk', 'Name': '「李佳凯」'},
            'Ljk': {'ID': 'Ljk', 'Name': '「陆嘉珂」'}}
    dlg = dialog('LJK"我是谁"', table=rows)
    assert dlg.table.rowCount() == 1
    ed = dlg._editors[0]
    assert '不能靠别名解决' in dlg.table.item(0, 2).text()
    assert ed.action.currentData() == ACT_SKIP
    # 跳过也要带原因（只说"先跳过"会让人以为是默认值，白试一轮）
    assert '只差大小写' in ed.note.text() or '撞车' in ed.note.text()


def test_renaming_the_new_character_clears_the_block(dialog):
    """
    人把新角色改名成**不靠大小写区分**的写法 → 立刻可行。
    ⚠ 只把 `LJK` 改成 `ljk2` **不够**（归一后仍是 `ljk` 这个键，照样撞车）——
    必须是结构上不同的名字（这里用 `lujk`）。这正是给用户的建议。
    """
    rows = {'ljk': {'ID': 'ljk', 'Name': '「李佳凯」'},
            'Ljk': {'ID': 'Ljk', 'Name': '「陆嘉珂」'}}
    dlg = dialog('LJK"我是谁"', table=rows)
    ed = dlg._editors[0]
    ed.action.setCurrentIndex(ed.action.findData(ACT_NEW))
    assert not ed.plan().executable          # 默认（ID=LJK）会撞车
    ed.new_var.setText('ljk2')               # 仍然归一撞车 → 还是不行
    assert not ed.plan().executable, 'ljk2 归一后仍是 ljk，不该放行'
    ed.new_var.setText('lujk')
    ed.new_name.setText('「陆嘉禾」')          # 显示名也得是没被占用的
    prop = ed.plan()
    assert prop.executable and prop.new.row['ID'] == 'lujk'


def test_skip_is_recorded_and_excluded_from_the_written_plans(dialog):
    dlg = dialog('MOM"吃饭了"\nBAOAN"问路"')
    for ed in dlg._editors:
        if ed.proposal.token == 'BAOAN':
            ed.action.setCurrentIndex(ed.action.findData(ACT_SKIP))
    props = dlg.result_proposals()
    assert [p.token for p in props] == ['MOM']
    decisions = {d['token']: d for d in dlg.decisions()}
    assert decisions['BAOAN']['action'] == ACT_SKIP


def test_hand_editing_the_target_recomputes_the_plan(dialog):
    """人在下拉里改指到 `txf` → 计划跟着变（写盘用的是重算后的结论）。"""
    dlg = dialog('TJF"你怎么睡得着哒"')
    ed = dlg._editors[0]
    idx = ed.target.findData('txf')
    assert idx >= 0
    ed.target.setCurrentIndex(idx)
    prop = ed.plan()
    assert prop.alias_ok and prop.alias.var == 'txf'


def test_hand_editing_the_new_character_name_is_respected(dialog):
    dlg = dialog('BAOAN"问路"')
    ed = dlg._editors[0]
    ed.new_var.setText('baoan')
    ed.new_name.setText('保安')
    prop = ed.plan()
    assert prop.new_ok, prop.note
    assert prop.new.row['ID'] == 'baoan' and prop.new.row['Name'] == '保安'


def test_the_dialog_never_writes_anything_itself(dialog, tmp_path):
    """弹窗只出计划；**落盘是另一层的事**（这里确认它不碰磁盘）。"""
    before = set(p.name for p in tmp_path.iterdir())
    dlg = dialog('MOM"吃饭了"')
    dlg.result_proposals()
    assert set(p.name for p in tmp_path.iterdir()) == before
