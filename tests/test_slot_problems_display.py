"""
把 `pattern.problems_of` 接进界面（2026-10-12）
==================================================

## 为什么有这条
`problems_of` 的文档自己写着"**界面直接显示**，不用自己拼"——
但全仓只有它自己引用了它：**界面从没调过**（又一个"实现了没接线"，
本仓这一模式已出现多次：S3/S4、候选搜索、5 秒回放、这个）。

为什么必须显示：这些毛病**不会让试切失败** —— 试切绿了、人却不知道为什么
不好使，正是用户反复卡住的地方。实测 `problems_of` 对"枚举格没填候选值"
报的是人话：

    第 2 格："从这些里选"要么给候选值，要么挂一个词组

用户截图里那三张卡（全是 choice、候选空着）拿到的就是这句 —— 他之前收不到。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture
def dlg(qapp, tmp_path):
    from storygraph.ui_qt.dialog_learn import CustomStatementDialog
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    return CustomStatementDialog(None, project_root=root, discovered=[])


def test_a_choice_slot_without_candidates_is_reported(dlg):
    """⭐ 用户卡住的那一格：选了「从这些里选」却既没候选值、也没挂词组。"""
    dlg.params.clear()
    dlg.params.add_param('', '服装', '', kind='choice')
    text = dlg._slot_problems()
    assert '候选值' in text or '词组' in text, text
    assert '第 1 格' in text, text


def test_a_clean_slot_list_says_nothing(dlg):
    """没毛病就别刷屏（噪声会让人无视整条提示）。"""
    dlg.params.clear()
    dlg.params.add_param('show', '', '', kind='keyword')
    dlg.params.add_param('小明', '角色', '', kind='variable')
    assert dlg._slot_problems() == ''


def test_problems_show_even_when_the_cut_succeeds(dlg):
    """
    ⭐⭐ **核心**：切得上**不代表**没问题 ——
    枚举格没填候选值时试切照样能成功，所以毛病必须**单独再说一遍**，
    否则人看到"绿的"就以为没事了。
    """
    dlg.params.clear()
    dlg.params.add_param('show', '', '', kind='keyword')
    dlg.params.add_param('小明', '角色', '', kind='variable')
    dlg.params.add_param('', '服装', '', kind='choice')     # 候选空着
    dlg.sample.setText('show 小明 泳装')
    dlg._refresh_cut()
    text = dlg.cut_view.text()
    assert '切出来是这样' in text, text          # 试切确实成功了
    assert '候选值' in text or '词组' in text, text   # 但毛病也说了


def test_problems_show_when_nothing_is_pasted_yet(dlg):
    """还没粘样例行时也给提示（人正站在这一步，最需要它）。"""
    dlg.params.clear()
    dlg.params.add_param('', '服装', '', kind='choice')
    dlg.sample.clear()
    dlg._refresh_cut()
    text = dlg.cut_view.text()
    assert '粘一行' in text and '候选值' in text, text


def test_problems_show_alongside_a_failed_cut(dlg):
    """切不上时，两条信息都要在（别用一个盖掉另一个）。"""
    dlg.params.clear()
    dlg.params.add_param('show', '', '', kind='keyword')
    dlg.params.add_param('', '服装', '', kind='choice')
    dlg.sample.setText('hide 小明')
    dlg._refresh_cut()
    text = dlg.cut_view.text()
    assert '切不上' in text, text
    assert '候选值' in text or '词组' in text, text


def test_this_wiring_is_reachable_from_the_ui_source():
    """
    ⚠ 反"死代码"判据：`problems_of` 必须在**界面源码**里被真的调用 ——
    它以前就是"文件里有、没人调"。
    """
    src = (ROOT / 'storygraph' / 'ui_qt' / 'dialog_learn.py').read_text(
        encoding='utf-8')
    assert 'problems_of' in src, '界面又没接 problems_of（它上次就是这样闲置的）'
