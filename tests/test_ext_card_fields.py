# -*- coding: utf-8 -*-
"""
一份字段 schema 驱动卡片右栏（2026-11-07 第三轮）
=================================================

用户 2026-11-07 的原话是三句，判据按这三句来：

> "我们首先要**统一三套字段表**，不能让它们互不相通……其次需要做一个动态的
>  自定义界面！" / "太散了！" / "卡片编辑器右边的 UI 和自定义模块定义出的
>  模块**一定要对应**！"

这一轮修的是"最后一米"：上一轮统一了**声明词汇**（`ext.schema.FIELD_KINDS`），
但"什么类型画什么控件"仍有两份实现（`dynamic_form._make_editor` 与
`card_editor.SPEC_EDITOR_KIND`），而且两份行为不一样。现在只有一份
（`ui_qt.field_widgets.make_editor`），判据**直接比控件类** —— 这是这条链上
唯一没法靠"注释说是一致的"糊过去的地方。

第二件事：`kind: "field"` 的扩展点以前**没有消费方**（和上一轮 `statement`
的断链同一类问题）。现在它改的是卡片上那一格的样子，值仍写回同一个字段。

判据分组：

  一、字段 schema 与卡片字段表**不许漂移**（少一格 / 多一格都要红）；
  二、两处界面画出来的**是同一个控件类**（含 `transition` 这个历史分歧）；
  三、`field` 扩展点真的能改卡片（内存里 / 真写 `ext.json` / 插件给的），
      并且关掉、写错名字、改成开关都不许静默。
"""
from __future__ import annotations

import json

import pytest

from storygraph.ext import card_fields as CF
from storygraph.ext.registry import MANIFEST, ExtensionRegistry
from storygraph.ext.schema import ExtensionPoint, FieldSpec, parse_field

# ==================================================================
#  一、schema 与卡片字段表不许漂移
# ==================================================================


class TestSchemaCoversTheCard:
    def test_every_advanced_field_has_a_spec(self):
        """
        高级区那几格**每一格都得在 schema 里**（否则它的样子又回到代码里）。

        这条是防漂移的主判据：`app.editor.ADVANCED_FIELDS` 加了一格而
        `ext.card_fields` 没跟上时，卡片会退回"普通输入框"——**能用，但外观
        跟字段表无关**，那正是这一轮要消灭的形态。
        """
        from storygraph.app.editor import ADVANCED_FIELDS

        specs = CF.builtin_card_fields()
        missing = [n for n in ADVANCED_FIELDS if n not in specs]
        assert not missing, (
            f'高级区这些字段没在 `ext.card_fields` 里声明：{missing}\n'
            f'（schema 是卡片右栏"长什么样"的唯一真源）')

    def test_spec_keys_are_exactly_the_schema(self):
        """内置 schema 的键 == `overridable_keys()`，且没有重复。"""
        keys = [spec.key for spec in CF.BUILTIN_CARD_FIELDS]
        assert len(keys) == len(set(keys)), f'schema 里有重复的键：{keys}'
        assert tuple(keys) == CF.overridable_keys()

    def test_bespoke_fields_are_not_in_the_schema(self):
        """
        专用控件的格**不许**混进字段表。

        说话人 / 台词 / 背景 / 立绘 / 台词转场那几格的"控件"是业务逻辑
        （候选来自项目、立绘要缩略图），一个 `kind` 描述不了。混进来只会
        得到"声明说它是输入框，实际是网格"的新一层不一致。
        """
        keys = set(CF.overridable_keys())
        clash = keys & set(CF.BESPOKE_CARD_FIELDS)
        assert not clash, f'这些格是专用控件，不该在字段表里：{sorted(clash)}'

    def test_primary_fields_are_not_in_the_schema(self):
        """主区的格（除备注/条件）同样是专用控件 —— 表里不该有它们。"""
        from storygraph.app.editor import PRIMARY_FIELDS

        keys = set(CF.overridable_keys())
        clash = (keys & set(PRIMARY_FIELDS)) - {'Comment'}
        assert not clash, f'主区这些格是专用控件，不该在字段表里：{sorted(clash)}'

    def test_placeholder_is_part_of_the_schema(self):
        """`placeholder` 能存能读（否则"字段表 → 界面"在最后一米又断）。"""
        spec = parse_field({'key': 'a', 'kind': 'variable',
                            'placeholder': '留空就没有'})
        assert spec.placeholder == '留空就没有'
        assert spec.to_dict()['placeholder'] == '留空就没有'
        # 没写就不进 JSON（老文件一个字节都不变）
        assert 'placeholder' not in parse_field(
            {'key': 'b', 'kind': 'variable'}).to_dict()


# ==================================================================
#  二、两处界面画出来的是同一个控件
# ==================================================================

#: 语义类型 → 期望的控件类名（**唯一一份**，与 `ext.schema.KIND_TO_WIDGET` 同源）
CASES = [
    ('variable', 'QLineEdit'),
    ('optional', 'QLineEdit'),
    ('expr', 'QLineEdit'),
    ('number', 'QLineEdit'),
    ('int', 'QLineEdit'),
    ('code', 'CodeEditor'),
    ('multiline', 'QPlainTextEdit'),
    ('choice', 'QComboBox'),
    ('bool', 'QCheckBox'),
    ('multi_choice', 'QWidget'),
]


def _form_widget(specs):
    """扩展窗口那一侧的控件（`DynamicForm` 是真界面，不是仿的）。

    ⚠ **必须把 form 一起返回**：只返回控件的话，`form` 出了这个函数就没引用
    了，Qt 会把它（连同里面的控件）一起销毁 —— 调用方拿到的是"已经被删掉的
    C++ 对象"（实测 `RuntimeError: Internal C++ object already deleted`）。
    """
    from storygraph.ui_qt.dynamic_form import DynamicForm

    form = DynamicForm(specs)
    return form, {key: editor for key, (_spec, editor) in form._editors.items()}


def _card_widget(panel, name):
    return panel._widgets[name]


@pytest.mark.parametrize('kind,expected', CASES)
def test_the_two_screens_build_the_same_widget_class(qapp, kind, expected):
    """
    同一个字段，扩展窗口与卡片上**必须是同一个控件类**。

    这条就是用户那句"定义出来的模块和卡片上一定要对应"的可执行版本：
    从前两边各有一条 if 链，`transition` 一边是下拉、一边是输入框 ——
    而这种不一致**不会报错**，只是"看起来不一样"。
    """
    from storygraph.ui_qt import field_widgets

    spec = FieldSpec(key='x', kind=kind, choices=('a', 'b'))
    _form, editors = _form_widget([spec])
    form = editors['x']
    # 卡片参数格：同一个 spec，走 `parser_only`（值要折回语句头）
    card = field_widgets.make_editor(spec, parser_only=True)
    assert type(form).__name__ == expected, f'{kind}: 扩展窗口画成了 ' \
                                            f'{type(form).__name__}'
    if kind in ('bool', 'multi_choice'):
        # 语句参数只能是"一个词"：这两类在参数格里**必须**退回输入框
        # （复选框读出来是 True / 列表，折不进脚本）
        assert type(card).__name__ == 'QLineEdit'
    else:
        assert type(card) is type(form), (
            f'{kind}: 两处画出来的控件不一样 '
            f'（窗口 {type(form).__name__} / 卡片 {type(card).__name__}）')


def test_transition_with_choices_is_a_dropdown_everywhere(qapp):
    """
    转场：有候选就该是**下拉**（两处一致）—— 这是历史上的分歧点。

    旧行为：卡片上是下拉（`SPEC_EDITOR_KIND['transition'] = 'combo'`），
    扩展窗口里是普通输入框。用户能看见的差别就是"同一格在两个窗口里不一样"。
    """
    from storygraph.ui_qt import field_widgets

    spec = FieldSpec(key='t', kind='transition', choices=('dissolve', 'fade'))
    _form, editors = _form_widget([spec])
    assert type(editors['t']).__name__ == 'QComboBox'
    assert type(field_widgets.make_editor(spec, parser_only=True)).__name__ \
        == 'QComboBox'
    # 没有候选时退回输入框（不能给一个空下拉）
    bare = FieldSpec(key='t', kind='transition')
    _form2, editors2 = _form_widget([bare])
    assert type(editors2['t']).__name__ == 'QLineEdit'


def test_fixed_word_is_readonly_everywhere(qapp):
    """
    固定字（`with` / `at`）两处都**只读**：改它等于改语句名。

    从前卡片上它是**可编辑**的（`_spec_cell_editor` 只看了"有没有候选"），
    人改一下就把写回时按位置替换的那个词改掉了。
    """
    from storygraph.ui_qt import field_widgets

    spec = parse_field({'key': 'with', 'kind': 'keyword'})
    _form, editors = _form_widget([spec])
    form = editors['with']
    card = field_widgets.make_editor(spec, parser_only=True)
    assert form.isReadOnly() and card.isReadOnly()
    assert form.text() == 'with' and card.text() == 'with'


def test_undeclared_kind_with_choices_is_still_a_combo(qapp):
    """
    老声明**没写类型、但有候选**的格仍然是下拉（别在统一的过程中弄丢）。

    从前卡片上是 `mode = SPEC_EDITOR_KIND.get(kind) or ('combo' if opts else
    'text')` —— 也就是说"有没有候选"曾经是一层**隐含的类型**。统一之后这层
    隐含规则必须显式化（`_spec_cell_editor` 里当 `choice` 处理），否则项目
    词汇表那几格会悄悄从下拉变成输入框。
    """
    from storygraph.ui_qt.card_editor import BeatCardEditor
    from storygraph.ui_qt.labeled_choices import as_choices

    panel = BeatCardEditor()
    try:
        opts = as_choices(['i11', 'i12'])
        w = panel._spec_cell_editor('位置', opts, None, '')
        assert type(w).__name__ == 'QComboBox', type(w).__name__
        # 一个候选都没有时才是输入框
        assert type(panel._spec_cell_editor('位置', [], None, '')).__name__ \
            == 'QLineEdit'
    finally:
        panel.deleteLater()


def test_values_round_trip_through_the_same_accessors(qapp):
    """读值 / 赋值也只有一份（`read_value` / `write_value`），四类都对。"""
    from storygraph.ui_qt import field_widgets

    cases = [
        (FieldSpec(key='a', kind='variable'), 'hello', 'hello'),
        (FieldSpec(key='a', kind='choice', choices=('x', 'y')), 'y', 'y'),
        (FieldSpec(key='a', kind='bool'), True, True),
        (FieldSpec(key='a', kind='multi_choice', choices=('x', 'y')),
         ['y'], ['y']),
    ]
    for spec, value, expected in cases:
        w = field_widgets.make_editor(spec)
        field_widgets.write_value(w, value)
        assert field_widgets.read_value(w) == expected, spec.kind


# ==================================================================
#  三、`field` 扩展点：改卡片上的那一格
# ==================================================================

def _field_point(key='Stage', **spec_kwargs):
    return ExtensionPoint(id='舞台动作', kind='field', label='舞台动作',
                          enabled=True, source='project',
                          fields=[FieldSpec(key=key, **spec_kwargs)])


class TestFieldExtensionPoint:
    def test_override_changes_the_row(self):
        """`Stage` 换成代码框 + 新标签 —— 纯函数那一层。"""
        specs, problems = CF.card_field_specs([_field_point(
            'Stage', label='舞台动作', kind='code', help='一行一条')])
        assert not problems, problems
        got = specs['Stage']
        assert got.label == '舞台动作'
        assert got.kind == 'code'
        assert '一行一条' in got.help
        assert '舞台动作' in got.help, 'tooltip 里要看得见"这一格被谁改过"'

    def test_partial_override_keeps_the_rest(self):
        """只写类型不写标签时，**标签不许变成空**（那会把格子弄丢名字）。"""
        specs, _ = CF.card_field_specs([_field_point('Stage', kind='code')])
        assert specs['Stage'].label == CF.builtin_card_fields()['Stage'].label
        assert specs['Stage'].placeholder \
            == CF.builtin_card_fields()['Stage'].placeholder

    def test_disabled_point_is_ignored(self):
        """关掉 = 没有这条覆盖（与其它三类扩展点同一条口径）。"""
        point = _field_point('Stage', kind='code', label='舞台动作')
        point.enabled = False
        specs, problems = CF.card_field_specs([point])
        assert specs['Stage'].kind == 'multiline'
        assert not problems

    def test_unknown_key_is_reported_not_dropped(self):
        """写错键名 → 进 problems（并列出能改的），原格**一个字都不变**。"""
        specs, problems = CF.card_field_specs([_field_point('Stageee')])
        assert specs['Stage'].kind == 'multiline'
        assert problems and 'Stageee' in problems[0]
        assert 'Stage' in problems[0], '报错里要直接给能写什么'

    def test_bespoke_key_is_refused_with_a_reason(self):
        """专用控件（说话人下拉）不许被字段表改 —— 并且说清为什么。"""
        specs, problems = CF.card_field_specs([_field_point('Speaker')])
        assert problems and '专用控件' in problems[0]
        assert 'Speaker' not in specs

    def test_switch_kind_is_refused(self):
        """把卡片上的格改成开关 → 拒（值写回时会是 `Stage=True`）。"""
        specs, problems = CF.card_field_specs([_field_point('Stage',
                                                            kind='bool')])
        assert problems and '开关' in problems[0]
        assert specs['Stage'].kind == 'multiline'

    def test_plugin_source_works_like_a_project_point(self):
        """插件给的拍字段扩展点与项目自己写的**同一条路**（不必改核心代码）。"""
        point = _field_point('Stage', kind='code', label='插件改的舞台')
        point.source = 'plugin:闪电动画.json'
        specs, problems = CF.card_field_specs([point])
        assert not problems
        assert specs['Stage'].label == '插件改的舞台'

    def test_builtin_projection_does_not_override(self):
        """工具自带 / 旧清单投影（只带开关）不产生覆盖 —— 它们的外观归原处。"""
        point = _field_point('Stage', kind='code')
        point.source = 'builtin'
        specs, _ = CF.card_field_specs([point])
        assert specs['Stage'].kind == 'multiline'

    def test_card_rebuilds_the_row_with_the_new_spec(self, qapp):
        """
        卡片那一侧：换 schema → 控件换成代码框 / 标签换成新的 / 值没丢。
        """
        from PySide6.QtWidgets import QLabel

        from storygraph.ui_qt.card_editor import BeatCardEditor

        panel = BeatCardEditor()
        try:
            assert type(panel._widgets['Stage']).__name__ == 'QPlainTextEdit'
            panel._set_value('Stage', 'show eileen at left')
            specs, _ = CF.card_field_specs([_field_point(
                'Stage', kind='code', label='舞台动作')])
            panel.set_card_field_specs(specs)
            assert type(panel._widgets['Stage']).__name__ == 'CodeEditor'
            # 值搬过去了（重建控件最容易丢的就是这个）
            assert panel._get_value('Stage') == 'show eileen at left'
            # 行标签真的换了（不是只改了 schema）
            labels = [lab.text() for lab in panel._adv_host.findChildren(QLabel)]
            assert '舞台动作' in labels, labels
        finally:
            panel.deleteLater()

    def test_same_schema_does_not_rebuild(self, qapp):
        """
        形状没变 → **一个控件都不许换**（重建会丢光标/选区，还会抢窗口焦点）。

        打开项目与扩展窗口保存完都会调一次 `set_card_field_specs`，而两次的
        schema 通常一模一样 —— 无条件重建会把"正在打字的那一格"换掉。
        """
        from storygraph.ui_qt.card_editor import BeatCardEditor

        panel = BeatCardEditor()
        try:
            before = panel._widgets['Stage']
            panel.set_card_field_specs(CF.builtin_card_fields())
            assert panel._widgets['Stage'] is before, '形状没变却重建了控件'
            # 真的变了才重建
            specs, _ = CF.card_field_specs([_field_point('Stage', kind='code')])
            panel.set_card_field_specs(specs)
            assert panel._widgets['Stage'] is not before
        finally:
            panel.deleteLater()

    def test_collapsed_advanced_still_writes_back(self, qapp):
        """换了 schema 之后，收起高级区照样写回（这条以前钉过 `SayTail`）。"""
        from storygraph.ui_qt.card_editor import BeatCardEditor

        panel = BeatCardEditor()
        try:
            specs, _ = CF.card_field_specs([_field_point('Stage', kind='code')])
            panel.set_card_field_specs(specs)
            panel._set_value('Stage', 'show eileen at right')
            panel._adv_host.setVisible(False)      # 模拟人点了收起
            fields = panel.collect_fields()
            assert fields.get('Stage') == 'show eileen at right'
        finally:
            panel.deleteLater()


# ==================================================================
#  四、端到端：真写一份 storygraph.ext.json
# ==================================================================

EXT = {
    'schema': 'storygraph.ext/1',
    'extensions': [
        {'id': '舞台动作', 'kind': 'field', 'label': '舞台动作',
         'enabled': True,
         'fields': [{'key': 'Stage', 'label': '舞台动作', 'kind': 'code',
                     'help': '一行一条 stage 指令（来自项目扩展点）'}]},
        {'id': '写错了的一格', 'kind': 'field', 'label': '写错了的一格',
         'enabled': True, 'fields': [{'key': 'Stageee'}]},
    ],
}


class TestProjectManifest:
    def test_project_file_changes_the_card_rows(self, tmp_path):
        """
        磁盘上只有一份 `storygraph.ext.json`（**没有改任何 Python**）——
        打开项目后卡片那一格就是新的样子。
        """
        (tmp_path / MANIFEST).write_text(
            json.dumps(EXT, ensure_ascii=False), encoding='utf-8')
        specs, problems = CF.for_project(tmp_path)
        assert specs['Stage'].kind == 'code'
        assert specs['Stage'].label == '舞台动作'
        # 写错的那个**不许静默**：它进了 problems（界面上会打到控制台）
        assert any('Stageee' in text for text in problems), problems

    def test_broken_manifest_still_opens_the_card(self, tmp_path):
        """坏 JSON → 卡片用内置 schema（**不许因此画不出卡片**）。"""
        (tmp_path / MANIFEST).write_text('{ 这不是 json', encoding='utf-8')
        specs, problems = CF.for_project(tmp_path)
        assert specs['Stage'].kind == 'multiline'
        assert problems, '坏文件要说一声'

    def test_no_project_root_uses_builtin(self):
        specs, problems = CF.for_project(None)
        assert specs['Stage'].kind == 'multiline'
        assert not problems

    def test_saving_through_the_registry_is_enough(self, tmp_path):
        """
        走**真装配线**：`ExtensionRegistry.save()` 落盘 → `for_project` 读回。

        这条防的是"界面能存、卡片读不到"——两个方向各读一份文件的经典断链。
        """
        reg = ExtensionRegistry.for_project(tmp_path)
        reg.upsert(_field_point('Stage', kind='code', label='舞台动作'))
        assert reg.save(tmp_path).get('ok'), reg.problems
        # `for_project` 每次都真读盘（缓存只在 `statement_registry` 那一层），
        # 所以这里不需要清缓存 —— 这条判据就是"写完立刻读得回来"。
        specs, problems = CF.for_project(tmp_path)
        assert not problems, problems
        assert specs['Stage'].kind == 'code'


# ==================================================================
#  五、扩展窗口：新建时能选种类
# ==================================================================

class TestExtensionDialogKind:
    def test_new_point_can_be_a_field_point(self, qapp, tmp_path):
        """
        「种类」下拉里四类都在，切到"拍字段"时说明就换成拍字段那套说法。

        2026-11-07 前这里**没有这个下拉**：`_new_point` 写死 `kind='statement'`，
        于是调用 / 拍字段 / 批处理在界面上根本建不出来（只能手改 JSON）——
        和"UI 根本没有给我这个功能"是同一句话。
        """
        from storygraph.ext.schema import KINDS
        from storygraph.ui_qt.dialog_extensions import ExtensionDialog

        dlg = ExtensionDialog(None, project_root=tmp_path)
        try:
            kinds = [dlg.cb_kind.itemData(i) for i in range(dlg.cb_kind.count())]
            assert kinds == list(KINDS)
            dlg.cb_kind.setCurrentIndex(dlg.cb_kind.findData('field'))
            assert 'Stage' in dlg.kind_note.text(), dlg.kind_note.text()
        finally:
            dlg.deleteLater()

    def test_kind_is_written_back(self, qapp, tmp_path):
        """窗口里选了"拍字段"，保存下来的那条就是拍字段（不是写法）。"""
        from storygraph.ui_qt.dialog_extensions import ExtensionDialog

        dlg = ExtensionDialog(None, project_root=tmp_path)
        try:
            reg = ExtensionRegistry.for_project(tmp_path)
            point = ExtensionPoint(id='我的舞台', kind='statement',
                                   source='project')
            reg.upsert(point)
            dlg._reg = reg
            dlg._reload_tree()
            dlg._load(point)
            dlg.cb_kind.setCurrentIndex(dlg.cb_kind.findData('field'))
            dlg._apply_to(point)
            assert point.kind == 'field'
        finally:
            dlg.deleteLater()
