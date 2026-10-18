"""
试切必须**先说自己**，再谈候选（2026-10-17 用户现场）
======================================================

现场（截图）::

    粘一行: show lan base_swimsuit happy        →  ⚠ 切不上
    D. 这样写对吗：· 服装（服装） 候选：… base_swimsuit …
                   · 表情（表情） 候选：… happy …
    → "这些格没吃到东西：服装、表情；第 3 格勾了只能从这里选，
       而这一行里的词不在候选里"

人看到的是**自相矛盾**：预览里明明列着 `base_swimsuit` / `happy`，
试切却说"候选外"。真凶不是候选，而是**第 2 格选了「可出现可不出现的一段」**：

  · 那类格子旧版**没有"是哪几个字"的输入框** → `text` 恒为空；
  · `pattern` 对空固定段的处理是"跳过"（可选段本来就可以不出现）→
    它一个词都吃不掉、也不报错；
  · 于是后面的格子整体错位，最后由"表情"的候选背锅；
  · 而预览只列候选、不列类型 —— 人只能一遍遍改候选，永远改不对。

这一组判据钉住三件事：
  ① 固定段必须有地方写"是哪几个字"，且**与列名分开**（列名不该被迫写成 `show`）；
  ② 死格要先说出来（比"候选外"更接近原因），并给出改法；
  ③ 被限死挡下的词，要说清它**本来是哪一格的**候选（否则人会去改错地方）。
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
    d = CustomStatementDialog(None, project_root=root, discovered=[])
    d.name.setText('show')
    d.params.clear()
    return d


def _cut(dlg, line: str) -> str:
    dlg.sample.setText(line)
    dlg._refresh_cut()
    return dlg.cut_view.text()


# ==================== ① 固定段能写字，且与列名分开 ====================

def test_可选段没写固定字时先骂这一格_而不是骂候选(dlg):
    """截图那个形状：可选段没字 → 一个词都吃不掉 → 后面全错位。"""
    dlg.params.add_param('角色', '角色', 'lan,lyj', kind='choice')
    dlg.params.add_param('', '服装', '', kind='optional')      # 没写固定字
    face = dlg.params.add_param('表情', '表情', 'happy,sad', kind='choice')
    face._limit.setChecked(True)

    text = _cut(dlg, 'show lan base_swimsuit happy')
    assert '切不上' in text, text
    assert '没写"是哪几个字"' in text, text
    assert '永远对不上' in text, text
    # 不再一上来就把锅甩给第 3 格的候选：先说第 2 格（死格），再说**那个词**
    #
    # ⚠ 2026-10-17 第二轮攻击后这一行的**分配**变了：语义层允许"跳过垃圾词
    #   去认自己的候选"，于是 `表情` 拿到 `happy`、`base_swimsuit` 真的没人认领
    #   （旧口诀是让 `表情` 整个空着、由"候选外"背锅）。原因没变、还更准：
    #   先点名死格，再点名**第 3 个词**，不再提第 3 格的候选。
    assert '第 2 格' in text, text
    assert text.index('第 2 格') < text.index('第 3 个词'), text
    assert 'base_swimsuit' in text and '没人认领' in text, text
    assert '表情' not in text.split('；')[0], text


def test_可选段填上固定字就能切(dlg):
    """`at left` 这种段：`at` 是固定字、`left` 才是值。"""
    dlg.params.add_param('角色', '角色', 'lan,lyj', kind='choice')
    slot = dlg.params.add_param('', '位置', '', kind='optional')
    slot._literal.setText('at')

    text = _cut(dlg, 'show lan at left')
    assert '切出来是这样' in text, text
    assert 'at left' in text, text


def test_固定字的输入框和列名是两件事(dlg):
    """列名是「动作」，剧本里要写的字是 `show` —— 别再逼人把列名写成 show。"""
    kw = dlg.params.add_field('', '', '', kind='keyword')
    kw._literal.setText('show')
    kw._name.setText('动作')
    dlg.params.add_param('角色', '角色', 'lan,lyj', kind='choice')

    slots = dlg.params.slots()
    assert slots[0]['kind'] == 'keyword'
    assert slots[0]['text'] == 'show', slots[0]
    assert slots[0]['column'] == '动作', slots[0]

    text = _cut(dlg, 'show lan')
    assert '切出来是这样' in text, text
    assert 'show（动作）' in text, text


def test_粘整行时_格子自己就是那个固定字就别摘(dlg):
    """
    `_refresh_cut` 会摘掉行首的动作名（方便人粘整行）。

    但如果**第一格本来就要那个字**，再摘一次就是偷走它的词 ——
    第 1 格永远对不上，而人会以为是自己写错了。
    """
    kw = dlg.params.add_field('', '', '', kind='keyword')
    kw._literal.setText('show')
    dlg.params.add_param('角色', '角色', 'lan,lyj', kind='choice')

    text = _cut(dlg, 'show lan')
    assert '切出来是这样' in text, text
    assert '共 2 格' in text, text
    assert 'show（固定）' in text, text


# ==================== ② 预览要写出"这一格是什么" ====================

def test_预览写出每格的类型_不然有候选会被读成能用(dlg):
    dlg.params.add_param('', '服装', '', kind='optional')
    dlg.params.add_param('', '位置', '', kind='optional')._literal.setText('at')
    kw = dlg.params.add_field('', '', '', kind='keyword')
    kw._literal.setText('show')
    kw._name.setText('动作')

    text = dlg.preview.toPlainText()
    assert '可选（没填字！）' in text, text
    assert '可选「at」' in text, text
    assert '固定「show」' in text, text

# ==================== ③ 挡下的词要说清"它本来是谁的" ====================

def test_被限死挡下的词要指出它属于哪一格(dlg):
    """
    人看到"候选外"的第一反应是去补候选 —— 但真正要改的可能是**顺序/类型**。
    所以必须说：这个词在第 N 格的候选里。
    """
    dlg.params.add_param('角色', '角色', 'lan', kind='choice')
    face = dlg.params.add_param('表情', '表情', 'afraid', kind='choice')
    face._limit.setChecked(True)
    # ⚠ 2026-10-18 起 `show` 的**属性格默认可不填**（`show ye happy` 在真实项目
    #   里出现 11 次，Ren'Py 的属性本来就可选）。而这一条钉的是"被限死挡下时
    #   怎么说" —— 必须让这一格**明确必填**，否则它空着就合法，根本轮不到"挡下"。
    face._optional.setChecked(False)
    dlg.params.add_param('服装', '服装', 'base_swimsuit', kind='choice')

    text = _cut(dlg, 'show lan base_swimsuit happy')
    assert '切不上' in text, text
    assert '第 2 格「表情」' in text, text
    assert '但它就是第 3 格「服装」的候选' in text, text
    assert '顺序' in text, text


def test_勾了限死却没候选时说清谁都不可能通过(dlg):
    dlg.params.add_param('角色', '角色', 'lan', kind='choice')
    face = dlg.params.add_param('表情', '表情', '', kind='choice')
    face._limit.setChecked(True)

    text = _cut(dlg, 'show lan happy')
    assert '一个候选都没给' in text, text


# ==================== ④ 候选口径：分栏给的也要认 ====================

def test_分栏里的候选照样用于试切_即使组名不是合法标识符(dlg):
    """
    `built_groups()` 会校验组名（写盘用），但**试切不该因此少候选** ——
    否则界面列着、试切不认，正是这次用户看到的自相矛盾。

    这里用中文组名（`build_group` 判它非法）钉住"试切仍认它"。
    """
    dlg.columns_editor.add_column('服装', '服装', 'base_swimsuit、clothes2')
    slot = dlg.params.add_param('', '服装', '', kind='choice')
    slot._limit.setChecked(True)

    text = _cut(dlg, 'show base_swimsuit')
    assert '切出来是这样' in text, text
    assert 'base_swimsuit（服装）' in text, text


def test_候选外的值必须在界面上看得出来(dlg):
    """
    `pattern._cell` 的 `ok` 以前**恒真**，于是"值不在候选里"在界面上没有位置放：
    人看到的是一排绿格子，库里存下一条不存在的服装/表情。

    ⚠ 这一条钉的是**接线**（`_cell.ok` → 试切那一行），不是分配规则：
      纯函数那边 `test_cut_tokenizer_attack2::test_值不在候选里时格子必须能被标出来`
      管 `ok` 算得对不对，这里管它有没有**真的显示出来**。
    """
    dlg.params.add_param('角色', '角色', 'lan,lyj', kind='choice')
    outfit = dlg.params.add_param('服装', '服装', 'base_swimsuit',
                                  kind='choice')
    # ⚠ 同上（2026-10-18）：属性格默认"可不填"之后，空着是免费的（0 点），
    #   于是 DP 不会再为了标黄去接一个候选外的词 —— 那本来就只是"建议"。
    #   这一条钉的是"**必填格**吃到了候选外的值时，界面要看得出来"，所以明确必填。
    outfit._optional.setChecked(False)
    dlg.params.add_param('表情', '表情', 'happy', kind='choice')

    text = _cut(dlg, 'show lan 校服 happy')
    assert '切出来是这样' in text, text
    assert '校服（服装，不在候选里）' in text, text
    # 候选里的值仍然是干净的（别把每个格子都标上）
    assert 'happy（表情）' in text, text


# ==================== ⑤ 纯函数那一侧 ====================

def test_match_line_说得出是哪个词被挡下():
    from storygraph.authoring import pattern as P
    got = P.match_line(
        [{'kind': 'variable', 'column': '角色'},
         {'kind': 'choice', 'column': '表情', 'limit': True,
          'options': ['afraid']},
         {'kind': 'choice', 'column': '服装', 'options': ['base_swimsuit']}],
        'lan base_swimsuit happy')
    assert got['ok'] is False
    assert got['rejected'] == 1
    assert got['rejected_word'] == 'base_swimsuit', got


def test_空固定段的原因不是对不上某个字():
    from storygraph.authoring import pattern as P
    got = P.match_line([{'kind': 'keyword'}], 'lan')
    assert got['ok'] is False
    assert '没写"是哪几个字"' in got['reason'], got
