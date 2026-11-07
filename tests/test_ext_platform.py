# -*- coding: utf-8 -*-
"""扩展层（`storygraph.ext`）的判据。

要钉住的是**用户 2026-11-07 点名的四件事**：

1. 四个入口是**一种东西**（写法 / 技术调用 / 拍字段 / 批处理）；
2. 每条都有「是否使用」「是否能够批处理」两个开关，且**真的生效**；
3. 字段表**动态**（加一格 → 界面/卡片多一格），不认识的键**不许静默吞**；
4. 插件能加东西，且**坏插件不连坐**。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from storygraph.ext import registry as ER
from storygraph.ext import schema as ES


# ----------------------------------------------------------------------
#  1. 形状与校验
# ----------------------------------------------------------------------

def test_a_statement_extension_carries_its_fields():
    point = ES.parse_extension({
        'id': 'mycue', 'kind': 'statement', 'label': '预约镜头',
        'fields': [
            {'key': '文字', 'label': '文字', 'kind': 'text'},
            {'key': '转场', 'kind': 'expr'},
        ],
    })
    assert [f.key for f in point.field_specs()] == ['文字', '转场']
    assert point.field('转场').kind == 'expr'
    assert point.source == 'project'


def test_unknown_key_is_refused_not_swallowed():
    """**不猜**：不认识的键整条拒绝（与 `batch_edit` 同一纪律）。"""
    with pytest.raises(ES.ExtError):
        ES.parse_extension({'id': 'x', 'kind': 'statement', 'whatever': 1})
    with pytest.raises(ES.ExtError):
        ES.parse_extension({'id': 'x', 'kind': 'statement',
                            'fields': [{'key': 'a', 'colour': 'red'}]})


def test_bad_kind_and_empty_choice_are_refused():
    with pytest.raises(ES.ExtError):
        ES.parse_extension({'id': 'x', 'kind': 'magic'})
    with pytest.raises(ES.ExtError):
        ES.parse_extension({'id': 'x', 'kind': 'statement',
                            'fields': [{'key': 'a', 'kind': 'choice'}]})


def test_slot_kind_maps_to_one_widget_kind():
    """解析器的一个"格" → 一条链：**语义类型 / 控件 / 解析器**，只有这一处映射。

    2026-11-07 第二轮：`FieldSpec.kind` 从"控件名"改成**语义类型**（与解析器
    同一个词），控件由 `widget` 推出来 —— 于是"声明里写什么"和"界面画什么"
    不再各说一套话。
    """
    field = ES.field_of_slot({'kind': 'word', 'column': '人物'})
    assert (field.kind, field.widget, field.parser_kind) == (
        'variable', 'text', 'variable')
    field = ES.field_of_slot({'kind': 'transition', 'column': '转场'})
    assert (field.kind, field.widget, field.parser_kind) == (
        'transition', 'expr', 'transition')
    field = ES.field_of_slot({'kind': 'number', 'column': '层级'})
    assert field.widget == 'number'
    field = ES.field_of_slot({'kind': 'choice', 'column': '声道',
                              'options': ['se', 'se2']})
    assert field.kind == 'choice' and field.choices == ('se', 'se2')
    # 固定字（`with`）也是字段：上一版这里整条扩展点会被丢掉
    field = ES.field_of_slot({'kind': 'keyword', 'text': 'with'})
    assert (field.kind, field.literal, field.widget) == ('keyword', 'with', 'text')


# ----------------------------------------------------------------------
#  2. 两个开关
# ----------------------------------------------------------------------

def test_enabled_and_batchable_gate_the_registry():
    reg = ER.ExtensionRegistry(points=[
        ES.parse_extension({'id': 'a', 'kind': 'call'}),
        ES.parse_extension({'id': 'b', 'kind': 'call', 'enabled': False}),
    ])
    assert [p.id for p in reg.enabled('call')] == ['a']
    reg.set_enabled('b', True)
    assert [p.id for p in reg.enabled('call')] == ['a', 'b']


def test_batch_rule_only_when_turned_on():
    point = ES.parse_extension({
        'id': 'master-dissolve', 'kind': 'batch', 'label': '主层过场',
        'fields': [{'key': '文本', 'kind': 'code'}],
        'batchable': False,
        'batch': {'when': {'show': 'sprite'},
                  'insert': {'text': '$ renpy.transition(Dissolve(0.1), '
                                     'layer="master")',
                             'placement': 'after_last_show'}},
    })
    # 关着 → 不产出规则（批处理不会偷偷跑）
    assert ES.to_cue_rule(point) == {}
    point.batchable = True
    rule = ES.to_cue_rule(point)
    assert rule['id'] == 'master-dissolve'
    assert 'renpy.transition' in rule['insert']['text']


# ----------------------------------------------------------------------
#  3. 装载：旧清单投影 / 覆盖 / 插件
# ----------------------------------------------------------------------

def _project(tmp_path: Path) -> Path:
    root = tmp_path / 'proj'
    root.mkdir()
    return root


def test_rules_json_is_projected_as_read_only_points(tmp_path):
    root = _project(tmp_path)
    (root / 'storygraph.rules.json').write_text(json.dumps({
        'statements': [{'name': 'mycue', 'label': '预约镜头',
                        'pattern': [{'kind': 'variable', 'column': '文字'}],
                        'clauses': [{'keyword': 'with', 'name': '转场',
                                     'kind': 'transition'}]}],
        'calls': [{'head': 'my_wipe', 'label': '擦除',
                   'columns': [{'name': '方向', 'position': 0},
                               {'name': '层', 'kw': 'layer'}]}],
    }, ensure_ascii=False), encoding='utf-8')

    reg = ER.ExtensionRegistry.for_project(root)
    mycue = reg.get('mycue')
    assert mycue is not None and mycue.kind == 'statement'
    assert [f.key for f in mycue.field_specs()] == ['文字', '转场']
    wipe = reg.get('my_wipe')
    assert wipe is not None and wipe.kind == 'call'
    assert [f.key for f in wipe.field_specs()] == ['方向', '层']
    # 旧清单的投影**默认不落盘**
    assert all(p['id'] != 'mycue' for p in reg.payload()['extensions'])


def test_everything_a_declaration_says_is_at_least_visible(tmp_path):
    """声明里**没变成字段**的部分也要看得见（别名 / 块体允许 / 渲染）。

    用户 2026-11-07："自定义写法能完全被它显示、兼容（不管它是代码还是别的
    什么东西）"。`pattern` / `clauses` 变成字段了，剩下的几项走 `docs`
    —— 先让它看得见，等能回写 `rules.json` 再升级成开关。
    """
    root = _project(tmp_path)
    (root / 'storygraph.rules.json').write_text(json.dumps({
        'statements': [{'name': 'mycue cue', 'label': '预约镜头',
                        'docs': '推到指定位置',
                        'block': True,
                        'render': 'verbatim',
                        'body': {'allows': ['say', 'show']}}],
    }, ensure_ascii=False), encoding='utf-8')

    point = ER.ExtensionRegistry.for_project(root).get('mycue')
    assert '推到指定位置' in point.docs
    assert 'cue' in point.docs            # 别名（名字里的第二个词）
    assert 'say' in point.docs and 'show' in point.docs   # 块体允许


def test_ext_manifest_wins_over_rules_json(tmp_path):
    root = _project(tmp_path)
    (root / 'storygraph.rules.json').write_text(json.dumps({
        'statements': [{'name': 'mycue', 'label': '旧名字'}],
    }, ensure_ascii=False), encoding='utf-8')
    (root / ER.MANIFEST).write_text(json.dumps({
        'schema': ES.EXT_SCHEMA,
        'extensions': [{'id': 'mycue', 'kind': 'statement',
                        'label': '新名字', 'enabled': False}],
    }, ensure_ascii=False), encoding='utf-8')

    reg = ER.ExtensionRegistry.for_project(root)
    assert reg.get('mycue').label == '新名字'
    assert reg.get('mycue').enabled is False


def test_builtin_renpy_transition_is_always_there(tmp_path):
    """引擎自带 API 不需要项目声明，但要能在同一个界面里被看见/停用。"""
    reg = ER.ExtensionRegistry.for_project(_project(tmp_path))
    point = reg.get('renpy.transition')
    assert point is not None and point.kind == 'call'
    assert point.source == 'builtin'
    assert [f.key for f in point.field_specs()] == ['transition', 'layer']


def test_plugins_json_and_py_both_load(tmp_path):
    root = _project(tmp_path)
    folder = root / 'extensions'
    folder.mkdir()
    (folder / 'a.json').write_text(json.dumps({'extensions': [
        {'id': 'from_json', 'kind': 'call', 'label': 'JSON 插件'}]},
        ensure_ascii=False), encoding='utf-8')
    (folder / 'b.py').write_text(
        'EXTENSIONS = [{"id": "from_py", "kind": "statement",'
        ' "label": "PY 插件"}]\n', encoding='utf-8')

    reg = ER.ExtensionRegistry.for_project(root)
    assert reg.get('from_json').source == 'plugin:a.json'
    assert reg.get('from_py').source == 'plugin:b.py'


def test_a_broken_plugin_does_not_take_the_others_down(tmp_path):
    root = _project(tmp_path)
    folder = root / 'extensions'
    folder.mkdir()
    (folder / 'bad.py').write_text('raise RuntimeError("炸了")\n',
                                   encoding='utf-8')
    (folder / 'good.json').write_text(json.dumps({'extensions': [
        {'id': 'ok', 'kind': 'call'}]}, ensure_ascii=False), encoding='utf-8')

    reg = ER.ExtensionRegistry.for_project(root)
    assert reg.get('ok') is not None
    assert any('bad.py' in p for p in reg.problems)


# ----------------------------------------------------------------------
#  4. 落盘
# ----------------------------------------------------------------------

def test_save_roundtrip_keeps_plugins_and_gates(tmp_path):
    root = _project(tmp_path)
    reg = ER.ExtensionRegistry.for_project(root)
    point = ES.parse_extension({
        'id': 'master-dissolve', 'kind': 'batch', 'label': '主层过场',
        'batchable': True,
        'batch': {'when': {'show': 'sprite'},
                  'insert': {'text': '$ renpy.transition(Dissolve(0.1), '
                                     'layer="master")',
                             'placement': 'after_last_show'}},
    })
    reg.upsert(point)
    res = reg.save(root)
    assert res['ok'], res

    again = ER.ExtensionRegistry.for_project(root)
    got = again.get('master-dissolve')
    assert got is not None and got.batchable is True
    assert 'renpy.transition' in got.batch['insert']['text']


def test_save_refuses_to_clobber_broken_json(tmp_path):
    root = _project(tmp_path)
    path = root / ER.MANIFEST
    path.write_text('{ 不是 json', encoding='utf-8')
    reg = ER.ExtensionRegistry.for_project(root)
    res = reg.save(root)
    assert res['ok'] is False
    assert path.read_text(encoding='utf-8') == '{ 不是 json'


def test_disabling_a_point_really_hides_it_from_the_tool(tmp_path):
    """「是否使用」不是一个装饰性复选框：关掉 = 注册表里真没有它。

    这是用户 2026-11-07 点名要的那两个开关之一，所以判据必须打到**消费方**
    （`StatementRegistry`），不能只断言 JSON 里写着 `enabled: false`。
    """
    from storygraph.story import statement_registry as SR

    root = _project(tmp_path)
    (root / 'storygraph.rules.json').write_text(json.dumps({
        'statements': [{'name': 'mycue', 'label': '预约镜头'}],
        'calls': [{'head': 'my_wipe', 'columns': [{'name': '方向',
                                                   'position': 0}]}],
    }, ensure_ascii=False), encoding='utf-8')

    SR.clear_cache()
    reg = SR.StatementRegistry.for_project(root)
    assert reg.match('mycue') is not None
    assert reg.call_spec('my_wipe') is not None

    # 只关 mycue（调用照旧用）
    (root / ER.MANIFEST).write_text(json.dumps({
        'schema': ES.EXT_SCHEMA,
        'extensions': [{'id': 'mycue', 'kind': 'statement', 'enabled': False}],
    }, ensure_ascii=False), encoding='utf-8')
    SR.clear_cache()
    reg = SR.StatementRegistry.for_project(root)
    assert reg.match('mycue') is None          # 关了 → 工具不再认这个写法
    assert reg.call_spec('my_wipe') is not None  # 没关的照旧


def test_disabling_a_call_removes_its_columns(tmp_path):
    from storygraph.story import statement_registry as SR

    root = _project(tmp_path)
    (root / 'storygraph.rules.json').write_text(json.dumps({
        'calls': [{'head': 'my_wipe', 'columns': [{'name': '方向', 'position': 0}]}],
    }, ensure_ascii=False), encoding='utf-8')
    (root / ER.MANIFEST).write_text(json.dumps({
        'schema': ES.EXT_SCHEMA,
        'extensions': [{'id': 'my_wipe', 'kind': 'call', 'enabled': False}],
    }, ensure_ascii=False), encoding='utf-8')

    SR.clear_cache()
    reg = SR.StatementRegistry.for_project(root)
    assert reg.call_spec('my_wipe') is None
    assert SR.call_columns(reg, 'my_wipe') == []



# ----------------------------------------------------------------------
#  5. 界面：动态表单真的按字段表长
# ----------------------------------------------------------------------

def test_dynamic_form_renders_one_editor_per_field(qapp):
    from storygraph.ui_qt.dynamic_form import DynamicForm

    specs = [ES.parse_field({'key': '文字', 'kind': 'text'}),
             ES.parse_field({'key': '层级', 'kind': 'number'}),
             ES.parse_field({'key': '转场', 'kind': 'expr'}),
             ES.parse_field({'key': '硬字幕', 'kind': 'bool'})]
    form = DynamicForm(specs)
    assert set(form._editors) == {'文字', '层级', '转场', '硬字幕'}
    form.set_values({'文字': '闪电', '层级': '5'})
    assert form.values()['文字'] == '闪电'
    assert form.values()['层级'] == '5'
    assert form.problems() == []
    # 数字格写了非数字 → 界面自己就能拦（不必等业务层）
    form.set_values({'层级': '很高'})
    assert form.problems()


def test_dialog_lists_all_four_kinds_and_saves(qapp, tmp_path):
    pytest.importorskip('PySide6')
    from storygraph.ui_qt.dialog_extensions import ExtensionDialog

    root = _project(tmp_path)
    (root / 'storygraph.rules.json').write_text(json.dumps({
        'statements': [{'name': 'mycue'}],
        'calls': [{'head': 'my_wipe', 'columns': [{'name': '方向'}]}],
    }, ensure_ascii=False), encoding='utf-8')

    dlg = ExtensionDialog(None, project_root=str(root))
    kinds = {dlg.tree.topLevelItem(i).text(0)
             for i in range(dlg.tree.topLevelItemCount())}
    # 语句 / 技术调用 / （字段与批处理只有存在时才建组）—— 至少这两类在
    assert any('写法' in k for k in kinds), kinds
    assert any('技术调用' in k for k in kinds), kinds

    dlg._new_point()
    dlg.ed_id.setText('master-dissolve')
    dlg.ed_label.setText('主层过场')
    dlg.ck_batch.setChecked(True)
    dlg.ed_text.setPlainText('$ renpy.transition(Dissolve(0.1), layer="master")')
    dlg._save()
    dlg.close()

    saved = json.loads((root / ER.MANIFEST).read_text(encoding='utf-8'))
    ids = [e['id'] for e in saved['extensions']]
    assert 'master-dissolve' in ids
