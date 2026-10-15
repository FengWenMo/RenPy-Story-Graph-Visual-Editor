"""
**内置语句也能长出参数格**（2026-10-13 用户要求）
================================================================================

> 接下来做：连 show 这种内置语句也能在卡片上长出参数格，具体做法是这样的：
> 本来就默认存在且有的，那就保留，只改后面加进去的，加了什么，就长个什么参数，
> 没必要全部都更新

这句话里有**两条独立的规矩**，各钉一组判据：

  ① **合并而不是替换**：声明某条内置语句时，工具对它**本来就有**的参数形状
     （`camera` 的层名、`with` 的转场、`pause` 的秒数、`window` 的动作）
     **一律保留**，只把新加的**追加**在后面。修之前是"有声明就整个换成声明的"，
     等于"加一列"顺手"删掉三列"，而界面不会提醒你。
  ② **`show` 真的能用**：`show` 是被显式建模的内置语句（它的行进 `Beat.show`，
     不在 `opaque` 里），所以在那之前"你声明了 show"**永远建不出列**。
     这一组判据走真窗口：格子在、值对、**改一格真的折回 `Beat.show`**。

⚠ 这条链路上实测抓到**三个**各自独立的真 bug（都已修并钉在这里）：
  · `_beat_statement_head` 不看 `Beat.show` → 声明了也不建列；
  · `statement_head()` 不给 `show` 补动作名 → 格子长出来了但**全是空的**；
  · `EditorSession.apply` 把动态列折在 `cards_to_show` **之前** →
    改完保存又被立绘卡重建盖回去（"卡片上能改、改了不算"）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.story import statement_registry as SR


def _project(tmp_path, body: str = None) -> Path:
    """一个最小项目：rules.json 空壳 + 一章（默认一行 `show`）。"""
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    (root / 'storygraph.rules.json').write_text('{"statements": []}',
                                                encoding='utf-8')
    (root / 'game' / 'ch01.rpy').write_text(
        body if body is not None else
        'label start:\n'
        '    show lan swim_suit happy\n'
        '    "旁白。"\n'
        '    return\n', encoding='utf-8')
    return root


def _declare_show(root):
    SR.declare_statement(
        root, 'show', label='服装切换',
        columns=[{'name': '人物', 'label': '人物'},
                 {'name': '泳装', 'label': '服装'},
                 {'name': '神态', 'label': '神态'}],
        block=None, docs='',
        pattern=[{'kind': 'variable', 'text': '人物', 'column': '人物'},
                 {'kind': 'variable', 'text': '泳装', 'column': '服装'},
                 {'kind': 'variable', 'text': '神态', 'column': '神态'}])
    SR.clear_cache()


# ===========================================================================
#  ① 合并：本来就有的列必须留着（用户那句"本来就默认存在且有的，那就保留"）
# ===========================================================================

def test_merge_keeps_every_builtin_column_and_appends(tmp_path):
    """
    ⭐⭐ 用户那句话的字面判据：**内置列一个不少，新加的在后面**。

    `camera` 内置只有「层名」；声明里加一格「备注」之后，
    层名**必须在**（而且要还在第一位），备注追加在后面。
    """
    merged = SR.merge_builtin_columns(
        [{'name': 'layer', 'label': '层名'}],
        [{'name': 'note', 'label': '备注'}])
    assert [c['name'] for c in merged] == ['layer', 'note'], merged
    assert merged[0]['label'] == '层名', '内置列的显示名被动了'


def test_merge_replaces_only_the_same_name(tmp_path):
    """同名的按声明改（于是"就是我加的那一格"能一直改），别的**不动**。"""
    merged = SR.merge_builtin_columns(
        [{'name': 'layer', 'label': '层名', 'positional': 0},
         {'name': 'seconds', 'label': '秒数'}],
        [{'name': 'layer', 'label': '哪一层'}])
    assert [c['name'] for c in merged] == ['layer', 'seconds']
    assert merged[0]['label'] == '哪一层', '同名的那一列该按声明改'
    assert merged[1]['label'] == '秒数', '没提到的列不该被动'


def test_arg_columns_merges_instead_of_replacing(tmp_path):
    """
    ⭐ 走**真注册表**验一遍：这正是修之前会丢掉内置列的那条路。

    修之前 `arg_columns` 是"有声明就 `return spec_columns(...)`" ——
    声明 `camera` 一格，内置的「层名」就没了。
    """
    root = _project(tmp_path)
    SR.declare_statement(root, 'camera', label='镜头',
                         columns=[{'name': 'note', 'label': '备注'}],
                         block=None, docs='',
                         pattern=[{'kind': 'variable', 'text': '备注',
                                   'column': 'note'}])
    SR.clear_cache()
    reg = SR.StatementRegistry.for_project(str(root))
    names = [c['name'] for c in SR.arg_columns(reg, 'camera')]
    assert 'layer' in names, f'内置的「层名」被声明顶掉了：{names}'
    assert 'note' in names, names
    assert names.index('layer') < names.index('note'), '新加的该在后面'


def test_a_declaration_does_not_invent_columns_for_other_heads(tmp_path):
    """只影响被声明的那一条：别的内置语句的列一个都不多。"""
    root = _project(tmp_path)
    _declare_show(root)
    reg = SR.StatementRegistry.for_project(str(root))
    assert [c['name'] for c in SR.arg_columns(reg, 'pause')] == ['seconds']
    assert [c['name'] for c in SR.arg_columns(reg, 'camera')] == ['layer']


# ===========================================================================
#  ② `show`：真的长出格子、值对、改得动
# ===========================================================================

def test_declaring_show_grows_parameter_columns(tmp_path):
    """
    ⭐⭐ 这一条正是用户要的东西：**声明 `show` → 那一拍长出参数格**。

    ⚠ 为什么以前不行：`show` 是被显式建模的内置语句，它的行住在 `Beat.show`
    里（`'lan swim_suit happy'`，不含动作名），既不在 `opaque` 也没有
    `spec_head` —— `_beat_statement_head` 只认那两处，于是**恒为空**，
    `arg_columns` 拿不到头 → 一个格子都不长。
    """
    from storygraph.story.beat_model import Beat
    from storygraph.story.statement_registry import dynamic_spec_columns

    root = _project(tmp_path)
    reg0 = SR.StatementRegistry.for_project(str(root))
    b = Beat()
    b.kind = 'show'
    b.show = 'lan swim_suit happy'
    assert dynamic_spec_columns([b], reg0) == [], '没声明就不该长格子'

    _declare_show(root)
    reg = SR.StatementRegistry.for_project(str(root))
    headers = [c['header'] for c in dynamic_spec_columns([b], reg)]
    assert headers == ['show·人物@0', 'show·泳装@1', 'show·神态@2'], headers


def test_the_show_cells_read_the_real_values(tmp_path):
    """
    ⭐ 格子长出来还不够，**值必须对**。

    ⚠ 实测踩过：格子长了但**全是空的** —— `Beat.show` 存的是动作名**后面**
    那一串，而 `statement_head()` 直接返回它（没有 `show ` 前缀）→
    `positional_tokens` 把第一个参数当成语句头吃掉，整排错位。
    """
    from storygraph.story.beat_model import Beat
    from storygraph.story.statement_registry import dynamic_spec_columns

    root = _project(tmp_path)
    _declare_show(root)
    reg = SR.StatementRegistry.for_project(str(root))
    b = Beat()
    b.kind = 'show'
    b.show = 'lan swim_suit happy'
    assert b.statement_head() == 'show lan swim_suit happy', b.statement_head()
    cols = dynamic_spec_columns([b], reg)
    assert b.spec_arg_cells(cols) == ['lan', 'swim_suit', 'happy']


def test_editing_a_show_cell_goes_back_into_the_beat(tmp_path):
    """
    ⭐⭐ **改一格真的生效**（走 `fold_spec_arg_columns`，与 Excel 同一条路）。

    ⚠ 实测踩过：折回把**带 `show ` 的整串**写进了 `Beat.show`
    （那个字段只存动作名后面那串）→ 字段变成 `'show ling …'`，
    渲染再加一次前缀就是 `show show ling …`（语法错）。所以写回时要摘掉动作名。
    """
    from storygraph.story.beat_model import Beat, fold_spec_arg_columns

    b = Beat()
    b.kind = 'show'
    b.show = 'lan swim_suit happy'
    fold_spec_arg_columns(b, {'show·人物@0': 'ling'})
    assert b.show == 'ling swim_suit happy', b.show
    # 再改别的格子也不该丢东西
    fold_spec_arg_columns(b, {'show·泳装@1': 'uniform'})
    assert b.show == 'ling uniform happy', b.show
    # 渲染出来的是一行合法语句（不重复动作名）
    from storygraph.story.beat_render import render_beats_to_rpy
    text = str(render_beats_to_rpy([b]))
    assert 'show ling uniform happy' in text, text
    assert 'show show' not in text, text


def test_a_multi_value_show_beat_gets_no_parameter_cells(tmp_path):
    """
    ⭐ **多值拍（`show a; show b`）不长参数格** —— 不猜，也不许静默改数据。

    ⚠ 实测踩过（这是"改坏数据"那一类，比不生效严重）：
    `'lan swim_suit happy; ling uniform calm'` 改了第一格的「人物」之后变成
    `'ling swim_suit happy; uniform calm'` —— 第二行的 `ling` **没了**，
    因为整串被当成一个 token 序列、位置号全错。

    多值拍有自己的列（「每指令一列」立绘2/立绘3…，一次一张立绘卡），
    那条路管多值、这条路管单值，互不重叠。
    """
    from storygraph.story.beat_model import Beat
    from storygraph.story.statement_registry import dynamic_spec_columns

    root = _project(tmp_path, body=(
        'label start:\n'
        '    show lan swim_suit happy\n'
        '    show ling uniform calm\n'
        '    "旁白。"\n'
        '    return\n'))
    _declare_show(root)
    reg = SR.StatementRegistry.for_project(str(root))
    b = Beat()
    b.kind = 'show'
    b.show = 'lan swim_suit happy; ling uniform calm'
    assert b.statement_head() == '', '多值拍不该给出"一个"语句头'
    assert dynamic_spec_columns([b], reg) == [], '多值拍不该长参数格'


def test_the_window_card_shows_and_saves_show_cells(qapp, tmp_path):
    """
    ⭐⭐ **真窗口**：卡片上真的出现那几格，改一格**保存后真的变了**。

    ⚠⚠ 这条钉住的是最阴的那个 bug：`EditorSession.apply` 把动态列折在
    `cards_to_show(cards)` / `hydrate_from_dsl` **之前**，于是立绘卡重建
    `Beat.show` 时把刚折进去的参数**盖回原样** ——
    界面上格子能改、保存也"成功"，但值一个字都没生效。

    （前两条判据都测的是 `fold_spec_arg_columns` 直接调用，**测不到这个顺序问题** ——
    "单元对了、集成错了"的典型。所以这条必须走真窗口。）
    """
    from storygraph.ui_qt.main_window import MainWindowQt

    root = _project(tmp_path)
    _declare_show(root)
    ch = root / 'game' / 'ch01.rpy'

    class _Engine:
        project_root = root

        def get_registry(self):
            return None

    win = MainWindowQt(_Engine(), ch)
    win.resize(1500, 950)
    for _ in range(3):
        qapp.processEvents()
    win._show_index(0)
    for _ in range(3):
        qapp.processEvents()

    widgets = dict(win._panel._spec_widgets or {})
    assert widgets, '卡片上没长出 show 的参数格'
    assert set(widgets) == {'show·人物@0', 'show·泳装@1', 'show·神态@2'}, \
        sorted(widgets)

    def _value(w):
        return w.currentText() if hasattr(w, 'currentText') else w.text()

    assert [_value(widgets[h]) for h in sorted(widgets)] == \
        ['lan', 'swim_suit', 'happy'], \
        {h: _value(w) for h, w in widgets.items()}

    # 改一格 → 保存 → **必须真的进到拍里**
    for header, w in widgets.items():
        if '人物' in header:
            if hasattr(w, 'setCurrentText'):
                w.setCurrentText('ling')
            else:
                w.setText('ling')
    for _ in range(2):
        qapp.processEvents()
    win.save_current_beat()
    for _ in range(2):
        qapp.processEvents()
    beat = win._session.beats[0]
    assert beat.show == 'ling swim_suit happy', \
        f'卡片上改了却没进到拍里（顺序问题又回来了？）：{beat.show!r}'
    win.close()


def test_show_columns_do_not_disturb_a_plain_project(tmp_path):
    """
    通用性底线：**没声明就不长格子**（新功能不许污染老项目）。

    `show` 是最常见的一行，一旦"默认就长参数格"，整部戏的卡片都会多出一组
    谁也没要的输入框。
    """
    from storygraph.story.beat_model import Beat
    from storygraph.story.statement_registry import dynamic_spec_columns

    root = _project(tmp_path)
    reg = SR.StatementRegistry.for_project(str(root))
    b = Beat()
    b.kind = 'show'
    b.show = 'lan swim_suit happy'
    assert dynamic_spec_columns([b], reg) == []
    assert b.spec_arg_cells([]) == []
