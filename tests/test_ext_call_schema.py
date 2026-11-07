# -*- coding: utf-8 -*-
"""**技术调用的参数形状也是字段**（2026-11-07 第五轮）

用户 2026-11-07 的原话："自定义模块一定要支撑起复杂的转场调用一类的"，
并且点名要"统一三套字段表"。前两轮把**写法**的格子（`pattern` / 子句）与
**拍字段**并进了一 `FieldSpec`；这一轮并掉最后一份 —— `calls[].columns[]`。

开工前探针 `.pip-tmp/probe_call_schema.py`（真装配 + 真窗口）给出的四件事：

  1. `rules.json` 的调用投影到扩展窗口时：`kind` 一律变 `variable`，
     `choices` / `position` / `kw` **全丢**，`effect` / `seconds_arg`
     根本没有地方放 —— 照着投影再存一份，`layer` 就成了第 3 个位置参数；
  2. 字段表只有 5 列，**没有位置**：新建的调用只能按行序，关键字只能靠
     "键名写成 `layer=`"这个私有记号；
  3. 人按 `columns` 的词去写 `{"key": "图层", "kw": "layer"}` →
     `字段里有不认识的键：kw` → **整条扩展点消失**（只在 problems 里留一句）；
  4. 舞台预览：扩展点里声明的调用**没有时长**（`effect` / `seconds_arg`
     存不下），没写 effect 时如实体现在"认不出"。

这个文件钉四件事，每条都打到**消费方**（解析器 / 卡片读值 / 舞台预览 /
真窗口），不是断言 JSON 形状：

  A. 一份 schema：`FieldSpec.position` / `FieldSpec.kw`，与 `columns[]` 互逆；
  B. 一处转换：`bridge.call_declarations` 只走 `column_of_field`；
  C. 一处存储：`storygraph.ext.json` 里 `position` / `kw` / `effect` /
     `seconds_arg` 往返不丢，老文件（`layer=`）不改意思；
  D. 两扇窗说同一句话：扩展窗口的「位置 / 关键字名」那一列，改完存盘，
     卡片读到的值 / 舞台预览的时长**真的跟着变**。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from storygraph.ext import bridge as EB
from storygraph.ext import registry as ER
from storygraph.ext import schema as ES
from storygraph.ext.schema import (ExtError, ExtensionPoint, FieldSpec,
                                   column_of_field, field_of_column,
                                   parse_extension, parse_field)
from storygraph.story import statement_registry as SR
from storygraph.story.beat_model import Beat, fold_spec_arg_columns

SCRIPT = '''\
label start:
    show lan happy
    $ my_wipe("left", 0.6, "screens")
    "台词"
'''

#: 一条**什么都有**的调用声明（位置 0 / 1、关键字 layer、候选、必填、
#: 演出效果与时长参数）—— 投影与往返都拿它当样本。
RULES_CALL = {
    'head': 'my_wipe', 'label': '擦除过场',
    'effect': 'Dissolve', 'seconds_arg': 1,
    'columns': [
        {'name': '方向', 'label': '方向', 'kind': 'choice',
         'choices': ['left', 'right'], 'position': 0, 'required': True},
        {'name': '秒数', 'label': '秒数', 'kind': 'number', 'position': 1},
        {'name': '图层', 'label': '图层', 'kw': 'layer'},
    ],
}


def _project(tmp_path: Path, ext=None, rules_calls=None,
             script: str = SCRIPT) -> Path:
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    (root / 'game' / 'script.rpy').write_text(script, encoding='utf-8')
    if ext is not None:
        (root / ER.MANIFEST).write_text(json.dumps(
            {'schema': ES.EXT_SCHEMA, 'extensions': list(ext)},
            ensure_ascii=False), encoding='utf-8')
    if rules_calls is not None:
        (root / 'storygraph.rules.json').write_text(
            json.dumps({'calls': list(rules_calls)}, ensure_ascii=False),
            encoding='utf-8')
    return root


def _reg(root) -> SR.StatementRegistry:
    SR.clear_cache()
    return SR.StatementRegistry.for_project(root)


def _normalized(columns) -> list:
    """`columns` 走一遍**唯一那份**规范化（`_calls_from_manifest`）。

    `kind` 也归一（`word` 与 `variable` 是同一个意思的两套词）——
    否则"往返不丢"会退化成"字符串一模一样"，而不是"意思一样"。
    """
    out = SR._calls_from_manifest([{'head': 'x', 'columns': list(columns)}])
    cols = out[0]['columns'] if out else []
    for col in cols:
        col['kind'] = ES.normalize_kind(col['kind']) or 'variable'
    return cols


# ---------------------------------------------------------------------------
#  A. 一份 schema
# ---------------------------------------------------------------------------

def test_a_call_field_carries_where_the_value_goes():
    """`position` / `kw` 是字段的两位（不是注释里的约定）。"""
    pos = parse_field({'key': '秒数', 'position': 1})
    kw = parse_field({'key': '图层', 'kw': 'layer'})
    assert (pos.position, pos.kw) == (1, '')
    assert (kw.position, kw.kw) == (None, 'layer')
    # 落盘往返：ext.json 里看得出这两句
    assert pos.to_dict()['position'] == 1
    assert kw.to_dict()['kw'] == 'layer'


@pytest.mark.parametrize('raw, expect', [
    ({'key': 'x', 'position': 0, 'kw': 'layer'}, '只能填一个'),
    ({'key': 'x', 'position': -1}, '不能是负数'),
    ({'key': 'x', 'position': '第一个'}, '必须是整数'),
    ({'key': 'x', 'kw': '2layer'}, '不是合法参数名'),
])
def test_a_broken_position_or_keyword_says_what_is_wrong(raw, expect):
    with pytest.raises(ExtError) as ei:
        parse_field(raw)
    assert expect in str(ei.value)


def test_a_field_without_a_kind_is_just_a_value():
    """`kind` 没写 → 任意值。**不许**因此把整条扩展点扔掉（探针 ③）。"""
    assert parse_field({'key': '图层', 'kw': 'layer'}).kind == 'variable'


def test_the_keyword_rule_has_one_definition():
    """`ext.schema.kw_ok` 与 `statement_registry.call_kw_ok` 不许漂。"""
    samples = ['layer', 'duration', '_x', 'a1', '', '2layer', 'a b',
               '图层', 'layer-x']
    assert [ES.kw_ok(x) for x in samples] == [SR.call_kw_ok(x) for x in samples]


@pytest.mark.parametrize('col', [
    {'name': '方向', 'position': 0},
    {'name': '方向', 'label': '方向', 'position': 0, 'kind': 'choice',
     'choices': ['left', 'right'], 'required': True},
    {'name': '秒数', 'position': 1, 'kind': 'number'},
    {'name': '图层', 'kw': 'layer'},
    {'name': '图层', 'label': '图层', 'kw': 'layer', 'kind': 'expr'},
    {'name': '转场', 'position': 0, 'kind': 'transition'},
])
def test_column_and_field_round_trip(col):
    """`columns` → 字段 → `columns`：意思一个都不丢（判据是"规范化后相等"）。"""
    back = column_of_field(field_of_column(col))
    assert _normalized([back]) == _normalized([col])


def test_a_legacy_key_ending_with_equals_is_still_a_keyword():
    """老记号（`layer=`）继续收 —— 老文件不能变意思。"""
    spec = parse_field({'key': 'layer=', 'label': '图层'})
    col = column_of_field(spec)
    assert (col['name'], col['kw'], col.get('position')) == ('layer', 'layer',
                                                             None)
    assert col['label'] == '图层'


def test_a_chinese_key_ending_with_equals_is_not_silently_dropped():
    """`图层=` 去掉等号不是合法参数名 → **按原名留成位置参数**，不整格丢掉。

    探针里就是这样：`kw='图层'` 被 `_calls_from_manifest` 判非法后整列消失，
    表格上那条调用少一个参数，谁也没说话。
    """
    spec = parse_field({'key': '图层=', 'label': '图层'})
    col = column_of_field(spec, position=2)
    assert 'kw' not in col
    assert (col['name'], col['position']) == ('图层=', 2)
    assert len(_normalized([col])) == 1


def test_an_explicit_position_does_not_depend_on_row_order():
    """位置写在第几位就是第几位（老文件没写才按行序）。"""
    points = [ExtensionPoint(id='my_wipe', kind='call', fields=[
        parse_field({'key': '第三个', 'position': 2}),
        parse_field({'key': '第一个', 'position': 0}),
        parse_field({'key': '图层', 'kw': 'layer'}),
    ])]
    cols = EB.call_declarations(points)[0]['columns']
    assert [(c['name'], c.get('position'), c.get('kw')) for c in cols] == [
        ('第三个', 2, None), ('第一个', 0, None), ('图层', None, 'layer')]


def test_a_row_without_position_falls_back_to_the_next_slot():
    """没写位置的格子按行序（关键字行不占位置）—— 老 ext.json 的行为不变。"""
    points = [ExtensionPoint(id='my_wipe', kind='call', fields=[
        FieldSpec(key='图层', kw='layer'),
        FieldSpec(key='方向'),
        FieldSpec(key='秒数'),
    ])]
    cols = EB.call_declarations(points)[0]['columns']
    assert [(c['name'], c.get('position'), c.get('kw')) for c in cols] == [
        ('图层', None, 'layer'), ('方向', 0, None), ('秒数', 1, None)]


# ---------------------------------------------------------------------------
#  B. 一处转换：投影进来 / 声明回去，是同一个形状
# ---------------------------------------------------------------------------

def test_the_projection_keeps_position_keyword_kind_and_choices(tmp_path):
    """`rules.json` 的调用 → 扩展点字段：两位都在，候选/类型/必填也在。"""
    root = _project(tmp_path, rules_calls=[RULES_CALL])
    point = ER.ExtensionRegistry.for_project(root).get('my_wipe')
    assert point is not None
    got = [(f.key, f.kind, f.position, f.kw, f.required, list(f.choices))
           for f in point.field_specs()]
    assert got == [('方向', 'choice', 0, '', True, ['left', 'right']),
                   ('秒数', 'number', 1, '', False, []),
                   ('图层', 'variable', None, 'layer', False, [])]
    assert (point.effect, point.seconds_arg) == ('Dissolve', 1)


def test_copying_a_rules_call_into_a_project_point_keeps_its_shape(tmp_path):
    """照着窗口里那条投影再存一份 —— 还是同一条调用（不是三条位置参数）。"""
    root = _project(tmp_path, rules_calls=[RULES_CALL])
    point = ER.ExtensionRegistry.for_project(root).get('my_wipe')
    copy = ExtensionPoint(id=point.id, kind='call', label=point.label,
                          source='project', fields=point.field_specs(),
                          effect=point.effect, seconds_arg=point.seconds_arg)
    back = EB.call_declarations([copy])[0]
    assert _normalized(back['columns']) == _normalized(RULES_CALL['columns'])
    assert back['effect'] == 'Dissolve'
    assert back['seconds_arg'] == 1


def test_the_builtin_call_shape_knows_which_parameter_is_the_keyword(tmp_path):
    """`renpy.transition` 的 `layer` 是关键字参数（内置形状也不许猜）。"""
    root = _project(tmp_path)
    point = ER.ExtensionRegistry.for_project(root).get('renpy.transition')
    assert point is not None
    by_key = {f.key: f for f in point.field_specs()}
    assert by_key['layer'].kw == 'layer'
    assert by_key['transition'].position == 0


# ---------------------------------------------------------------------------
#  C. 一处存储：ext.json 往返 + 老文件
# ---------------------------------------------------------------------------

def test_ext_json_round_trips_position_keyword_and_effect():
    raw = {'id': 'my_wipe', 'kind': 'call', 'label': '擦除',
           'effect': 'Dissolve', 'seconds_arg': 1,
           'fields': [{'key': '方向', 'position': 0, 'required': True},
                      {'key': '图层', 'kw': 'layer'}]}
    once = parse_extension(raw)
    twice = parse_extension(once.to_dict())
    assert twice.to_dict() == once.to_dict()
    assert once.to_dict()['fields'][0]['position'] == 0
    assert once.to_dict()['fields'][1]['kw'] == 'layer'
    assert once.to_dict()['seconds_arg'] == 1


def test_a_broken_seconds_arg_says_which_point():
    with pytest.raises(ExtError) as ei:
        parse_extension({'id': 'my_wipe', 'kind': 'call',
                         'seconds_arg': '第二个', 'fields': []})
    assert 'seconds_arg' in str(ei.value)


def test_saving_from_the_registry_keeps_the_new_bits(tmp_path):
    """`ExtensionRegistry.save` → 重新装配 → 还是那条形状。"""
    point = ExtensionPoint(id='my_wipe', kind='call',
                           fields=[parse_field({'key': '秒数', 'position': 2})],
                           effect='Fade', seconds_arg=1, source='project')
    root = _project(tmp_path)
    reg = ER.ExtensionRegistry([point], root=root)
    assert reg.save(root)['ok']
    saved = json.loads((root / ER.MANIFEST).read_text(encoding='utf-8'))
    item = saved['extensions'][0]
    assert item['fields'][0] == {'key': '秒数', 'label': '', 'kind': 'variable',
                                 'position': 2}
    assert (item['effect'], item['seconds_arg']) == ('Fade', 1)
    SR.clear_cache()
    cols = SR.call_columns(SR.StatementRegistry.for_project(root), 'my_wipe')
    assert [(c['call']['position'], c['call']['kw']) for c in cols] == [(2, '')]


def test_an_old_extension_file_with_layer_equals_still_means_a_keyword(tmp_path):
    """老写法（`key: "layer="`）装配出来仍是关键字参数（一个字节都不用改）。"""
    root = _project(tmp_path, ext=[{
        'id': 'my_wipe', 'kind': 'call',
        'fields': [{'key': '方向'}, {'key': 'layer='}]}])
    cols = SR.call_columns(_reg(root), 'my_wipe')
    assert [(c['name'], c['call']['kind'], c['call']['kw']) for c in cols] == [
        ('方向', 'pos', ''), ('layer', 'kw', 'layer')]


def test_a_declaration_that_omits_kind_still_reaches_the_registry(tmp_path):
    """省略 `kind` 的字段不再让整条扩展点消失（探针 ③ 的回归）。"""
    root = _project(tmp_path, ext=[{
        'id': 'my_wipe', 'kind': 'call',
        'fields': [{'key': '图层', 'kw': 'layer'}]}])
    assert [c['head'] for c in _reg(root).calls] == ['my_wipe']


# ---------------------------------------------------------------------------
#  D. 消费方：卡片读到的值 / 舞台预览的时长
# ---------------------------------------------------------------------------

def test_the_card_reads_the_argument_the_position_points_at(tmp_path):
    """只声明第 2 个参数 → 读到的就是第 3 个实参（不是第 0 个）。"""
    root = _project(tmp_path, ext=[{
        'id': 'my_wipe', 'kind': 'call',
        'fields': [{'key': '第三个', 'position': 2}]}])
    beat = Beat(seq=1, label='start', kind='say',
                code='my_wipe("left", 0.6, "screens")')
    cols = SR.dynamic_spec_columns([beat], _reg(root))
    assert [c['header'] for c in cols] == ['my_wipe·第三个@2']
    assert beat.spec_arg_cells(cols) == ['screens']


def test_a_keyword_parameter_is_read_by_its_keyword(tmp_path):
    root = _project(tmp_path, ext=[{
        'id': 'my_wipe', 'kind': 'call',
        'fields': [{'key': '图层', 'kw': 'layer'}]}])
    beat = Beat(seq=1, label='start', kind='say',
                code='my_wipe("left", 0.6, layer="screens")')
    cols = SR.dynamic_spec_columns([beat], _reg(root))
    # 关键字列的列名带 `@kw`（`spec_args.dynamic_column_name` 的口径）
    assert [c['header'] for c in cols] == ['my_wipe·图层@kw']
    assert beat.spec_arg_cells(cols) == ['screens']


def test_editing_the_position_changes_which_value_is_read(tmp_path):
    """同一格：位置 0 → 读到 "left"；位置 1 → 读到 0.6。"""
    root = _project(tmp_path, ext=[{
        'id': 'my_wipe', 'kind': 'call',
        'fields': [{'key': '值', 'position': 0}]}])
    beat = Beat(seq=1, label='start', kind='say',
                code='my_wipe("left", 0.6)')
    cols = SR.dynamic_spec_columns([beat], _reg(root))
    assert beat.spec_arg_cells(cols) == ['left']
    fold_spec_arg_columns(beat, {cols[0]['header']: 'right'})
    assert beat.code == 'my_wipe("right", 0.6)'
    # 位置改成 1 → 同一格读第二实参
    point = ExtensionPoint(id='my_wipe', kind='call',
                           fields=[parse_field({'key': '值', 'position': 1})],
                           source='project')
    ER.ExtensionRegistry([point], root=root).save(root)
    beat2 = Beat(seq=1, label='start', kind='say',
                 code='my_wipe("left", 0.6)')
    cols2 = SR.dynamic_spec_columns([beat2], _reg(root))
    assert [c['header'] for c in cols2] == ['my_wipe·值@1']
    assert beat2.spec_arg_cells(cols2) == ['0.6']


def test_the_stage_preview_takes_its_duration_from_the_extension(tmp_path):
    """扩展点里写的 effect / seconds_arg 进舞台预览（不是只有 rules.json 算数）。"""
    from storygraph.app.stage import build_frames

    root = _project(tmp_path, ext=[{
        'id': 'my_wipe', 'kind': 'call', 'effect': 'Dissolve',
        'seconds_arg': 1,
        'fields': [{'key': '方向', 'position': 0},
                   {'key': '秒数', 'kind': 'number', 'position': 1}]}])
    beat = Beat(seq=1, label='start', kind='say', text='一',
                code='my_wipe("left", 0.6)')
    frame = build_frames([beat], project_root=root)[0]
    assert [t.source for t in frame.transitions] == ['declared-call']
    assert frame.effects[0].duration == pytest.approx(0.6)


def test_without_an_effect_the_preview_says_it_cannot_tell(tmp_path):
    """没写 effect → 如实体现在"认不出"，不假装淡入。"""
    from storygraph.app.stage import build_frames

    root = _project(tmp_path, ext=[{
        'id': 'my_wipe', 'kind': 'call',
        'fields': [{'key': '方向', 'position': 0}]}])
    beat = Beat(seq=1, label='start', kind='say', text='一',
                code='my_wipe("left", 0.6)')
    frame = build_frames([beat], project_root=root)[0]
    assert [t.source for t in frame.transitions] == ['declared-call']
    assert frame.effects[0].duration == pytest.approx(0.0)


# ---------------------------------------------------------------------------
#  E. 两扇窗：真窗口改，存盘，消费方跟着变
# ---------------------------------------------------------------------------

def _dialog(qapp, root):
    from storygraph.ui_qt.dialog_extensions import ExtensionDialog

    dlg = ExtensionDialog(None, project_root=root)
    return dlg


def test_the_field_table_shows_where_each_parameter_goes(qapp, tmp_path):
    from storygraph.ui_qt import dialog_extensions as DE

    root = _project(tmp_path, rules_calls=[RULES_CALL])
    dlg = _dialog(qapp, root)
    assert dlg.focus_on('my_wipe')
    header = dlg.tbl.horizontalHeaderItem(DE._F_POS).text()
    assert '位置' in header and '关键字' in header
    assert [dlg.tbl.item(r, DE._F_POS).text()
            for r in range(dlg.tbl.rowCount())] == ['0', '1', 'layer']
    dlg.close()


def test_the_position_column_is_locked_for_statements(qapp, tmp_path):
    from PySide6.QtCore import Qt
    from storygraph.ui_qt import dialog_extensions as DE

    root = _project(tmp_path, ext=[{
        'id': 'mycue', 'kind': 'statement',
        'fields': [{'key': '文字', 'kind': 'variable'}]}])
    dlg = _dialog(qapp, root)
    assert dlg.focus_on('mycue')
    item = dlg.tbl.item(0, DE._F_POS)
    assert not (item.flags() & Qt.ItemIsEditable)
    assert '技术调用' in dlg.tbl.horizontalHeaderItem(DE._F_POS).text()
    assert not dlg.meta_row.isVisibleTo(dlg)
    dlg.close()


def test_typing_a_position_in_the_window_changes_the_read_value(qapp,
                                                               tmp_path):
    """**主判据**：窗口里改「位置 / 关键字名」→ 存盘 → 卡片读到另一个值。"""
    from storygraph.ui_qt import dialog_extensions as DE

    root = _project(tmp_path, ext=[{
        'id': 'my_wipe', 'kind': 'call',
        'fields': [{'key': '值', 'position': 0}]}])
    dlg = _dialog(qapp, root)
    assert dlg.focus_on('my_wipe')
    dlg.tbl.item(0, DE._F_POS).setText('1')
    dlg._save()
    assert dlg.changed
    dlg.close()
    beat = Beat(seq=1, label='start', kind='say',
                code='my_wipe("left", 0.6)')
    cols = SR.dynamic_spec_columns([beat], _reg(root))
    assert [c['header'] for c in cols] == ['my_wipe·值@1']
    assert beat.spec_arg_cells(cols) == ['0.6']


def test_typing_a_keyword_name_in_the_window_makes_a_keyword(qapp, tmp_path):
    from storygraph.ui_qt import dialog_extensions as DE

    root = _project(tmp_path, ext=[{
        'id': 'my_wipe', 'kind': 'call',
        'fields': [{'key': '图层'}]}])
    dlg = _dialog(qapp, root)
    assert dlg.focus_on('my_wipe')
    dlg.tbl.item(0, DE._F_POS).setText('layer')
    dlg._save()
    dlg.close()
    saved = json.loads((root / ER.MANIFEST).read_text(encoding='utf-8'))
    assert saved['extensions'][0]['fields'][0]['kw'] == 'layer'
    assert 'position' not in saved['extensions'][0]['fields'][0]
    beat = Beat(seq=1, label='start', kind='say',
                code='my_wipe("left", 0.6, layer="screens")')
    cols = SR.dynamic_spec_columns([beat], _reg(root))
    assert beat.spec_arg_cells(cols) == ['screens']


def test_the_call_effect_row_reaches_the_stage_preview(qapp, tmp_path):
    """窗口里填「等价于哪个转场 / 时长取第几个参数」→ 预览真的按它演。"""
    from storygraph.app.stage import build_frames

    root = _project(tmp_path, ext=[{
        'id': 'my_wipe', 'kind': 'call',
        'fields': [{'key': '方向', 'position': 0},
                   {'key': '秒数', 'kind': 'number', 'position': 1}]}])
    dlg = _dialog(qapp, root)
    assert dlg.focus_on('my_wipe')
    assert dlg.meta_row.isVisibleTo(dlg)
    dlg.ed_effect.setText('Dissolve')
    dlg.sp_seconds.setValue(1)
    dlg._save()
    dlg.close()
    beat = Beat(seq=1, label='start', kind='say', text='一',
                code='my_wipe("left", 0.6)')
    frame = build_frames([beat], project_root=root)[0]
    assert frame.effects[0].duration == pytest.approx(0.6)


def test_saving_an_old_file_normalises_the_equals_mark(qapp, tmp_path):
    """老 ext.json（`layer=`）在窗口里存一次 → 变成 `kw: layer`，意思不变。"""
    root = _project(tmp_path, ext=[{
        'id': 'my_wipe', 'kind': 'call',
        'fields': [{'key': '方向', 'position': 0}, {'key': 'layer='}]}])
    dlg = _dialog(qapp, root)
    assert dlg.focus_on('my_wipe')
    assert [dlg.tbl.item(r, 0).text() for r in range(2)] == ['方向', 'layer=']
    dlg._save()
    dlg.close()
    saved = json.loads((root / ER.MANIFEST).read_text(encoding='utf-8'))
    fields = saved['extensions'][0]['fields']
    assert fields[1]['kw'] == 'layer'
    assert fields[1]['key'] == 'layer='
    cols = SR.call_columns(_reg(root), 'my_wipe')
    assert [(c['name'], c['call']['kind']) for c in cols] == [
        ('方向', 'pos'), ('layer', 'kw')]


def test_the_two_windows_produce_the_same_columns(qapp):
    """调用窗口那一张小表 → `columns`，与扩展窗口的转换是同一份 schema。"""
    from storygraph.ui_qt.call_params import CallParamsDialog

    columns = [{'name': '方向', 'label': '方向', 'position': 0},
               {'name': '秒数', 'label': '秒数', 'position': 1},
               {'name': '图层', 'label': '图层', 'kw': 'layer'}]
    dlg = CallParamsDialog(columns, head='my_wipe')
    built, bad = dlg.built()
    assert not bad
    dlg.close()
    fields = [field_of_column(col) for col in built]
    assert [column_of_field(f) for f in fields] == built
