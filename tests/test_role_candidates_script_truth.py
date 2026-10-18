"""
角色的**三种写法**：候选表里写的是哪一种，以及剧本里真要写的是哪一种。

用户 2026-10-18 现场（截图里那两行）：

    ⚠ 切不上：第 1 格「角色」的 28 个候选里没有「lan」
    > 啧……还是有问题！明明它只是换成变量名又不认得了……

真凶不是"工具不认识这个词"，而是**候选表存的不是脚本里能写的那种写法**：

    角色表            变量名 `l`     显示名 `蓝`     立绘 tag `lan`
    候选（旧版写进去）         ✗            ✓              ✗
    剧本里 show 要写            ✗            ✗              ✓

旧版（`90f6328` 之前）把**显示名**写进了声明，于是他既切不上、卡片下拉里
挑一个还会把 `蓝` 写进脚本（Ren'Py 里没有这个 tag）。这一条钉住的是
"载入存量声明时把它换成剧本真源"（`learn_statement.role_candidates_for`
+ `dialog_learn._fill_params`），以及"换不掉时说清是同一个人换了写法"
（`explain_role_spelling`）。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest


def _proj(tmp_path, characters=None):
    """一个只带角色表的最小工程（`game/asset_tables/characters.xlsx`）。"""
    from storygraph.assets.asset_registry import (_TABLE_FIELDS,
                                                  write_dict_to_excel)
    root = tmp_path / 'proj'
    target = root / 'game' / 'asset_tables'
    target.mkdir(parents=True, exist_ok=True)
    write_dict_to_excel(
        target / 'characters.xlsx',
        characters or {
            'l': {'ID': 'l', 'Name': '蓝', 'ImageTag': 'lan'},
            'jc': {'ID': 'jc', 'Name': '蒋忱', 'ImageTag': 'jc'},
            'mm': {'ID': 'mm', 'Name': '妈妈'},          # 没有 ImageTag 的老表行
        },
        _TABLE_FIELDS['character'])
    return root


def _decl(columns = None):
    """旧形状的声明：只有 `columns`（没有 `pattern`）—— 用户那份 rules.json 就长这样。"""
    return SimpleNamespace(
        name='show', label='', docs='', block=None, pattern=None,
        columns=columns if columns is not None else [
            {'name': '角色', 'label': '角色', 'choices': ['蓝', '蒋忱']},
            {'name': '服装', 'label': '服装', 'choices': ['base_swimsuit']},
            {'name': '表情', 'label': '表情', 'choices': ['happy']},
        ])


# ---------------------------------------------------------------------------
#  纯逻辑：候选值 → 剧本真源
# ---------------------------------------------------------------------------

def test_显示名候选_换成_show_要写的立绘_tag(tmp_path):
    from storygraph.authoring.learn_statement import role_candidates_for

    got = role_candidates_for(_proj(tmp_path), 'show', ['蓝', '蒋忱'])
    assert got['values'] == ['lan', 'jc'], got
    assert got['labels'] == {'lan': '蓝', 'jc': '蒋忱'}, got['labels']
    assert got['changed'] == {'蓝': 'lan', '蒋忱': 'jc'}, got['changed']


def test_自定义语句里给的是变量名而不是_tag(tmp_path):
    """`swim_suit lyj "泳装"` 这类语句写的是**变量名**，不是立绘 tag。"""
    from storygraph.authoring.learn_statement import role_candidates_for

    got = role_candidates_for(_proj(tmp_path), 'swim_suit', ['蓝'])
    assert got['values'] == ['l'], got
    assert got['changed'] == {'蓝': 'l'}, got['changed']


def test_已经是剧本真源的值_一个都不动(tmp_path):
    from storygraph.authoring.learn_statement import role_candidates_for

    got = role_candidates_for(_proj(tmp_path), 'show', ['lan', 'jc'])
    assert got['values'] == ['lan', 'jc'], got
    assert got['changed'] == {}, '已经对了就别改，别制造"我什么都动了"的假象'
    assert got['labels'] == {'lan': '蓝', 'jc': '蒋忱'}, got['labels']


def test_不是角色的候选_原样留着(tmp_path):
    """「服装」「表情」两格的候选与角色表无关 —— 这条路对它们一个字都不能动。"""
    from storygraph.authoring.learn_statement import role_candidates_for

    got = role_candidates_for(_proj(tmp_path), 'show',
                              ['base_swimsuit', 'happy', 'hanfu_base'])
    assert got['values'] == ['base_swimsuit', 'happy', 'hanfu_base'], got
    assert got['changed'] == {}, got['changed']
    assert got['labels'] == {}, got['labels']


def test_没有角色表就一个字都不动(tmp_path):
    from storygraph.authoring.learn_statement import role_candidates_for

    empty = tmp_path / 'empty'
    empty.mkdir()
    got = role_candidates_for(empty, 'show', ['蓝'])
    assert got['values'] == ['蓝'], got['values']
    assert got['changed'] == {}, got['changed']


def test_两个人共用一个_tag_时去重但不丢证据(tmp_path):
    """
    真机 `narrator` 与 `n` 的立绘 tag 都是 `narrator` —— 归一之后只能留一个
    （脚本里写的就是 `show narrator`），但要记下丢了谁。
    """
    from storygraph.authoring.learn_statement import role_candidates_for

    root = _proj(tmp_path, {
        'narrator': {'ID': 'narrator', 'Name': 'narrator'},
        'n': {'ID': 'n', 'Name': 'n', 'ImageTag': 'narrator'},
    })
    got = role_candidates_for(root, 'show', ['narrator', 'n'])
    assert got['values'] == ['narrator'], got['values']
    assert got['dropped'] == ['n'], got['dropped']


def test_显示名带书名号也认(tmp_path):
    """角色表里的 `Name` 可能写成 `「蒋忱」`；候选里人可能连书名号一起贴。"""
    from storygraph.authoring.learn_statement import role_candidates_for

    root = _proj(tmp_path, {'jc': {'ID': 'jc', 'Name': '「蒋忱」',
                                   'ImageTag': 'jc'}})
    got = role_candidates_for(root, 'show', ['「蒋忱」', '蒋忱'])
    assert got['values'] == ['jc'], got['values']


# ---------------------------------------------------------------------------
#  "换不掉"时要说得清：不是顺序错，是同一个人换了一种写法
# ---------------------------------------------------------------------------

def test_explain_role_spelling_说清是哪一种写法(tmp_path):
    from storygraph.authoring.learn_statement import explain_role_spelling

    why = explain_role_spelling(_proj(tmp_path), 'show', 'lan', ['蓝', '蒋忱'])
    assert '「lan」' in why and '蓝' in why, why
    assert '立绘 tag' in why and '显示名' in why, why
    assert '角色表' in why, '要给出可执行的下一步（重新导入角色表）'


def test_explain_role_spelling_不是角色就不说话(tmp_path):
    from storygraph.authoring.learn_statement import explain_role_spelling

    assert explain_role_spelling(_proj(tmp_path), 'show', 'base_swimsuit',
                                 ['蓝']) == ''
    # 候选里根本没有同一个人的另一种写法 → 也没什么可解释的
    assert explain_role_spelling(_proj(tmp_path), 'show', 'lan', ['jc']) == ''


# ---------------------------------------------------------------------------
#  端到端：弹窗载入那份存量声明之后，`show lan …` 必须切得上
# ---------------------------------------------------------------------------

def test_载入存量声明_角色候选换成剧本真源_试切切得上(qapp, tmp_path):
    from PySide6.QtWidgets import QPushButton

    from storygraph.ui_qt.dialog_learn import CustomStatementDialog

    dlg = CustomStatementDialog(None, project_root=str(_proj(tmp_path)),
                                discovered=[])
    dlg._load_statement(_decl())
    card = dlg.params._cards[0]

    # ① 候选换成了剧本真源（写盘只有 `lan`），芯片仍然看得懂（`蓝（lan）`）
    assert card._values.text() == 'lan,jc', card._values.text()
    chips = [b.text() for b in card._values.findChildren(QPushButton)]
    assert any('蓝（lan）' in t for t in chips), chips

    # ② 这件事**说出来**了，不是静默改写
    note = dlg._role_fix_note.text()
    assert '蓝 → lan' in note, note
    assert '保存' in note, '要说清"保存才写回清单"'

    # ③ 写回清单的也是真源（卡片下拉以后再也不会把显示名写进脚本）
    cols = dlg.built()['columns']
    assert cols[0]['choices'] == ['lan', 'jc'], cols[0]

    # ④ 用户那一行
    dlg.sample.setText('show lan base_swimsuit happy')
    dlg._refresh_cut()
    text = dlg.cut_view.text()
    assert '切不上' not in text, text
    assert 'lan（角色）' in text, text
    assert 'base_swimsuit（服装）' in text, text
    assert 'happy（表情）' in text, text


def test_载入_show_声明时_服装和表情的候选一个字都不动(qapp, tmp_path):
    from storygraph.ui_qt.dialog_learn import CustomStatementDialog

    dlg = CustomStatementDialog(None, project_root=str(_proj(tmp_path)),
                                discovered=[])
    dlg._load_statement(_decl())
    cards = dlg.params._cards
    assert cards[1]._values.text() == 'base_swimsuit', cards[1]._values.text()
    assert cards[2]._values.text() == 'happy', cards[2]._values.text()
    # 没换过的那两格不该出现在提示里
    assert '服装' not in dlg._role_fix_note.text(), dlg._role_fix_note.text()


def test_候选本来就对的时候_不冒提示(qapp, tmp_path):
    from storygraph.ui_qt.dialog_learn import CustomStatementDialog

    dlg = CustomStatementDialog(None, project_root=str(_proj(tmp_path)),
                                discovered=[])
    dlg._load_statement(_decl([
        {'name': '角色', 'label': '角色', 'choices': ['lan', 'jc']},
    ]))
    assert dlg.params._cards[0]._values.text() == 'lan,jc'
    assert dlg._role_fix_note.text() == '', dlg._role_fix_note.text()


def test_切不上时_面板会说清是同一个人的另一种写法(qapp, tmp_path):
    """
    还有一条路会走到"候选里是显示名"：人**手打/粘**进去的候选（不走载入）。
    这时不能只说"候选里没有" —— 那是把人往"改顺序"上带。
    """
    from storygraph.ui_qt.dialog_learn import CustomStatementDialog

    dlg = CustomStatementDialog(None, project_root=str(_proj(tmp_path)),
                                discovered=[])
    dlg.params.clear()
    dlg.name.setText('show')
    card = dlg.params.add_param('', '角色', '蓝,蒋忱', kind='choice')
    card._limit.setChecked(True)                  # 封闭枚举才会"挡词"
    dlg.sample.setText('show lan')
    dlg._refresh_cut()
    text = dlg.cut_view.text()
    assert '切不上' in text, text
    assert '立绘 tag' in text and '显示名' in text, text
    assert '角色表' in text, text


@pytest.mark.parametrize('head', ['show', 'scene', 'hide'])
def test_三个立绘语句都按_tag_归一(tmp_path, head):
    from storygraph.authoring.learn_statement import role_candidates_for

    assert role_candidates_for(_proj(tmp_path), head, ['蓝'])['values'] == ['lan']
