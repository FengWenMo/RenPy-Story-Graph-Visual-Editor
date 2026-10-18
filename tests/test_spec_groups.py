"""
词组分组（"服装 / 表情分开"）判据
=================================

用户 2026-10-10：

> 实际上需要分组显示……但因为我这个要求通用！所以一般人其实是没有服装这个
> 选项的！所以事实上应当通过 B 这个自定义模块，做到把服装跟表情这种东西分开！

所以这一套的**主语是项目**：项目在 `storygraph.rules.json` 的 `attr_groups`
里声明"有哪几组词"，工具只管按声明分栏。这个文件钉住四层：

  ① `authoring.spec_groups`（纯函数）：归属规则 + "没归到的词留在原地"；
  ② `ui_qt.choice_picker`：组标题**不可选**、筛选时空组连标题一起消失、
     不给 groups 时行为与从前**一字不差**；
  ③ `app.beats.expression_groups_for`：值走**界面显示名**（与 chip 同源）；
  ④ 声明端到端：`attr_groups` 写进清单 → 注册表读到 → 会话交给前端。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.authoring.spec_groups import (                        # noqa: E402
    AttrGroup, group_choices, group_of, group_report, group_values,
    parse_attr_groups)
from storygraph.story import statement_registry as SR                 # noqa: E402

GROUPS = [AttrGroup(name='outfit', label='服装',
                    options=['swim_suit', 'uniform']),
          AttrGroup(name='expression', label='表情',
                    options=['happy', 'calm', 'sad'])]


# ==================== ① 纯函数 ====================

def test_parse_reads_the_declared_groups():
    """
    只认**对象**形式的组声明；其它一律跳过（与 `_from_manifest` 的"坏条目跳过"
    同一口径）—— 尤其别把裸字符串当成"一个组"，否则用户手抖写错就会多出一栏。
    """
    got = parse_attr_groups([
        {'name': 'outfit', 'label': '服装', 'options': ['swim_suit', 'uniform']},
        {'name': 'rest', 'label': '其他'},
        {'label': '没名字'},                 # 没名字 → 跳过
        '裸字符串', 42, None,                # 都不是组声明 → 跳过
    ])
    assert [g.name for g in got] == ['outfit', 'rest']
    assert got[0].options == ['swim_suit', 'uniform']
    assert got[1].is_rest, '没写 options = 兜底组'
    assert parse_attr_groups('不是列表') == []
    assert parse_attr_groups(None) == []


def test_exact_and_token_match():
    assert group_of('swim_suit', GROUPS) == 'outfit'
    assert group_of('happy', GROUPS) == 'expression'
    # 整词命中：`swim_suit_glad` 里 `swim_suit` 是完整一段
    assert group_of('swim_suit_glad', GROUPS) == 'outfit'


def test_no_substring_matching():
    """
    ⚠ 刻意**不做**子串包含：`unhappy` 里含 `happy`，按子串会把它归进"开心" ——
    这正是 `Symbols.attr_for` 当初踩过的坑（猜错一个表情就是演出事故）。
    """
    assert group_of('unhappy', GROUPS) == '', 'unhappy 不该被当成 happy'


def test_wildcard_prefix():
    groups = parse_attr_groups([{'name': 'outfit', 'label': '服装',
                                 'options': ['swim_*']}])
    assert group_of('swim_suit', groups) == 'outfit'
    assert group_of('swim_body_glad', groups) == 'outfit'
    assert group_of('uniform', groups) == ''


def test_ungrouped_values_stay_in_place():
    """没归到组的词**留在原地**（单独一组、没有标题）—— 分组绝不等于藏词。"""
    got = group_choices(['swim_suit', 'happy', 'blankface'], GROUPS)
    assert [g['group'] for g in got] == ['outfit', 'expression', '']
    assert got[-1]['values'] == ['blankface'], got
    assert got[-1]['label'] == ''


def test_rest_group_collects_the_ungrouped():
    groups = GROUPS + [AttrGroup(name='rest', label='其他')]
    got = group_choices(['swim_suit', 'happy', 'blankface'], groups)
    assert [g['group'] for g in got] == ['outfit', 'expression', 'rest']
    assert got[-1]['values'] == ['blankface']


def test_no_declaration_means_no_groups_at_all():
    """⚠ 通用性底线：没声明 → 一个组都不给（调用方走原来的平铺候选）。"""
    assert group_choices(['swim_suit', 'happy'], []) == []
    assert group_choices(['swim_suit', 'happy'], None) == []
    assert group_values(['swim_suit'], []) == []


def test_empty_groups_do_not_show_up():
    """声明了"服装"但这个角色没有泳装图 → 不该出现一个空的"服装"栏。"""
    got = group_values(['happy', 'calm'], GROUPS)
    assert [g['name'] for g in got] == ['expression'], got


def test_group_report_says_what_is_declared():
    text = group_report(GROUPS + [AttrGroup(name='rest', label='其他')])
    assert '服装' in text and '表情' in text and '兜底' in text
    assert '没有声明' in group_report([])


# ==================== ② 选择器（offscreen 真 Qt） ====================

def test_picker_inserts_unselectable_group_headers(qapp):
    from PySide6.QtCore import Qt
    from storygraph.ui_qt.choice_picker import ChoicePickerDialog

    groups = [{'group': 'outfit', 'label': '服装', 'values': ['swim_suit']},
              {'group': 'expression', 'label': '表情', 'values': ['happy']}]
    dlg = ChoicePickerDialog(None, title='选', choices=['swim_suit', 'happy'],
                             groups=groups)
    texts = [dlg._list.item(i).text() for i in range(dlg._list.count())]
    assert '── 服装 ──' in texts and '── 表情 ──' in texts
    # ⚠ 组标题**不可选**：否则回车/双击会把"服装"当成值写进脚本
    for i in range(dlg._list.count()):
        item = dlg._list.item(i)
        if item.text().startswith('──'):
            assert not (item.flags() & Qt.ItemIsSelectable), item.text()
    assert dlg._list.currentItem().text() == 'swim_suit', '默认要落在第一个真候选上'


def test_picker_drops_empty_groups_while_filtering(qapp):
    from storygraph.ui_qt.choice_picker import ChoicePickerDialog, filter_groups

    groups = [{'group': 'outfit', 'label': '服装', 'values': ['swim_suit']},
              {'group': 'expression', 'label': '表情', 'values': ['happy']}]
    assert [g['group'] for g in filter_groups(groups, 'swim')] == ['outfit']
    assert filter_groups(groups, 'zzz') == []
    dlg = ChoicePickerDialog(None, choices=[], groups=groups)
    dlg._filter.setText('happy')
    texts = [dlg._list.item(i).text() for i in range(dlg._list.count())]
    assert '── 服装 ──' not in texts, '筛没了的那一组连标题一起消失'
    assert '── 表情 ──' in texts and 'happy' in texts


def test_picker_without_groups_behaves_exactly_as_before(qapp):
    from storygraph.ui_qt.choice_picker import ChoicePickerDialog

    dlg = ChoicePickerDialog(None, choices=['a', 'b', 'c'])
    texts = [dlg._list.item(i).text() for i in range(dlg._list.count())]
    assert texts == ['a', 'b', 'c'], '不给 groups 时不许出现任何标题'


# ==================== ③ 显示名与 chip 同源 ====================

def test_group_values_use_the_same_display_words_as_the_chip(tmp_path):
    """
    ⚠ 分组候选的值必须是**界面显示值**（`choices_for_image()['expression']`
    那一套）—— 否则下拉里是 `happy` 而 chip 上是「开心」，两条路给出不一样的
    字面量（`expression_choices` 的 docstring 记着这个坑）。
    """
    from storygraph.assets.asset_registry import (_TABLE_FIELDS,            # noqa: E402
                                                 write_dict_to_excel)
    from storygraph.app.beats import (choices_for_image, expression_groups_for)
    from storygraph.pipeline.build import load_asset_registry
    import openpyxl

    adir = tmp_path / 'game' / 'asset_tables'
    adir.mkdir(parents=True)
    write_dict_to_excel(adir / 'images.xlsx',
                        {'lyj_swim': {'ID': 'lyj_swim',
                                      'Path': 'game/images/lyj/swim.png',
                                      'Tag': 'lyj', 'Attributes': 'swim_suit',
                                      'Category': 'character'},
                         'lyj_happy': {'ID': 'lyj_happy',
                                       'Path': 'game/images/lyj/happy.png',
                                       'Tag': 'lyj', 'Attributes': 'happy',
                                       'Category': 'character'}},
                        _TABLE_FIELDS['image'])
    # 项目词表：开心 → happy（显示值应当变成「开心」）
    write_dict_to_excel(adir / 'characters.xlsx', {}, _TABLE_FIELDS['character'])
    reg = load_asset_registry(tmp_path)
    symbols = reg and __import__('storygraph.story.beat_render',
                                 fromlist=['make_symbols']).make_symbols(reg)
    if symbols is None:
        pytest.skip('资源表没读出来')
    symbols.expressions['开心'] = 'happy'          # 项目声明的人话词

    flat = choices_for_image('lyj happy', symbols)['expression']
    grouped = expression_groups_for('lyj', symbols, GROUPS)
    grouped_vals = [v for g in grouped for v in g['values']]
    assert set(grouped_vals) <= set(flat), (grouped_vals, flat)
    assert 'swim_suit' in grouped_vals
    # 「开心」这种显示名一旦进了平铺候选，分组里也必须用同一个写法
    for g in grouped:
        for v in g['values']:
            assert v in flat, (v, flat)


# ==================== ④ 声明端到端 ====================

def test_groups_are_read_from_the_manifest(tmp_path):
    from storygraph.story import statement_registry as SR2
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    (root / 'storygraph.rules.json').write_text(json.dumps({
        'statements': [{'name': 'swim_suit', 'label': '泳装切换'}],
        'attr_groups': [{'name': 'outfit', 'label': '服装',
                         'options': ['swim_suit']}],
    }, ensure_ascii=False), encoding='utf-8')
    SR2.clear_cache()
    reg = SR2.StatementRegistry.for_project(root)
    assert [g.name for g in reg.attr_groups] == ['outfit']
    assert reg.match('swim_suit') is not None, '词组与语句声明共存'
    # ⚠ 2026-10-18：这里原来是 `... or True`（永真），于是"报告里看不到分栏"
    #   一直没被测出来。现在报告必须**真的**带上分栏。
    report = SR2.format_registry_report(reg)
    assert '服装' in report and 'outfit' in report, report


def test_no_manifest_means_no_groups(tmp_path):
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    SR.clear_cache()
    assert SR.StatementRegistry.for_project(root).attr_groups == []


def test_declare_attr_groups_writes_and_keeps_other_keys(tmp_path):
    """词组与语句声明**同一份清单**：写词组不许动 `statements` 或用户自己的键。"""
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    (root / 'storygraph.rules.json').write_text(json.dumps({
        'statements': [{'name': 'swim_suit'}],
        '我的备注': '别弄丢我',
        'attr_groups': [{'name': 'outfit', 'label': '服装',
                         'options': ['swim_suit']}],
    }, ensure_ascii=False), encoding='utf-8')
    SR.clear_cache()
    res = SR.declare_attr_groups(root, [
        {'name': 'expression', 'label': '表情', 'options': ['happy', 'calm']},
        {'name': 'outfit', 'label': '服装', 'options': ['swim_suit', 'uniform']},
    ])
    assert res['ok'] and res['action'] == 'updated', res
    data = json.loads((root / 'storygraph.rules.json').read_text(encoding='utf-8'))
    assert data['我的备注'] == '别弄丢我'
    assert [s['name'] for s in data['statements']] == ['swim_suit']
    got = {g['name']: g for g in data['attr_groups']}
    assert got['outfit']['options'] == ['swim_suit', 'uniform'], '同名 = 更新'
    assert got['expression']['label'] == '表情'
    assert list(got) == ['outfit', 'expression'], '原有顺序保留，新的追加在后'


def test_declare_attr_groups_refuses_a_broken_manifest(tmp_path):
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    (root / 'storygraph.rules.json').write_text('{坏', encoding='utf-8')
    res = SR.declare_attr_groups(root, [{'name': 'x'}])
    assert not res['ok'] and 'JSON' in res['reason']
    assert (root / 'storygraph.rules.json').read_text(encoding='utf-8') == '{坏'


def test_declare_attr_groups_refuses_an_empty_list(tmp_path):
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    assert not SR.declare_attr_groups(root, [])['ok']


def test_the_dialog_carries_groups_into_the_declaration(qapp, tmp_path):
    """弹窗里那一节表 → 后端参数（只是纯函数调用；写盘在 `declare_attr_groups`）。"""
    from storygraph.authoring.learn_statement import build_group, group_preview
    from storygraph.ui_qt.dialog_learn import CustomStatementDialog

    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    dlg = CustomStatementDialog(None, project_root=root, discovered=[],
                                declared_groups=parse_attr_groups([
                                    {'name': 'outfit', 'label': '服装',
                                     'options': ['swim_suit']}]))
    # 已有声明会填进来（让人看到现在是怎么分的）
    # ⚠ 词组区已从"三列表格"换成"宽卡片"（用户 2026-10-11："表格太小了"）
    #   → 判据跟着走新 API（`count()` / `rows()`），验的契约没变。
    assert dlg.groups.count() == 1
    assert dlg.groups.rows()[0]['name'] == 'outfit'
    # ⚠ 2026-10-18 第六版：取消"把分栏一起写进清单"那个勾选框 ——
    #   分栏有自己那一页的保存按钮，语句的保存**永不碰分栏**
    #   （两套保存语义共用一个确定键，正是旧版"到底写了没写"的混乱来源）。
    got = dlg.groups_to_write()
    assert [g['name'] for g in got] == ['outfit']
    assert got[0]['options'] == ['swim_suit']
    assert group_preview(got).startswith('写进')
    # 新增一组
    dlg.groups.add_group('expression', '表情', 'happy, calm')
    names = [g['name'] for g in dlg.groups_to_write()]
    assert names == ['outfit', 'expression']
    bad = build_group('9坏', 'x', 'a')
    assert not bad['ok'] and bad['warnings']


def test_the_dialog_can_write_groups_only(qapp, tmp_path, monkeypatch):
    """
    只写分栏（语句名留空）也要能存 —— 用户可能只想分栏。

    ⚠ 现在走的是**分栏页自己的保存键**（`_save_columns_now`），
    不是"语句的保存键顺带把分栏写掉"。
    """
    from PySide6.QtWidgets import QMessageBox
    from storygraph.ui_qt.dialog_learn import CustomStatementDialog
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    dlg = CustomStatementDialog(None, project_root=root, discovered=[])
    assert not dlg._ok_btn.isEnabled(), '没有语句时不该能"保存语句"'
    dlg.groups.add_group('outfit', '服装', 'swim_suit')
    monkeypatch.setattr(QMessageBox, 'question',
                        staticmethod(lambda *a, **k: QMessageBox.Yes))
    dlg._save_columns_now()
    import json
    data = json.loads((root / 'storygraph.rules.json').read_text(encoding='utf-8'))
    assert [g['name'] for g in data['attr_groups']] == ['outfit']
    assert 'statements' not in data, '只保存分栏却写了语句'
    assert dlg.changed is True


def test_session_hands_the_groups_to_the_frontend(tmp_path):
    """会话只递数据：项目声明的组 → 前端拿到（没声明就是空表）。"""
    from storygraph.app.editor import EditorSession

    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    (root / 'storygraph.rules.json').write_text(json.dumps({
        'attr_groups': [{'name': 'outfit', 'label': '服装',
                         'options': ['swim_suit']}],
    }, ensure_ascii=False), encoding='utf-8')
    SR.clear_cache()
    s = EditorSession(project_root=root)
    groups = s.declared_attr_groups()
    assert [g.name for g in groups] == ['outfit']
    assert groups[0].label == '服装'
    # 没声明的项目 → 空表（前端走平铺，行为一字不变）
    other = tmp_path / 'other'
    (other / 'game').mkdir(parents=True)
    assert EditorSession(project_root=other).declared_attr_groups() == []
