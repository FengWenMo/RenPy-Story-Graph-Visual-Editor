# -*- coding: utf-8 -*-
"""**扩展点 → 运行时**：界面上配出来的东西，工具真的会用。

2026-11-07 探针（`.pip-tmp/probe_ext_runtime.py`）在上一轮之后实测出三处断链，
这个文件就是把它们钉住：

  1. `storygraph.ext.json` 里**新建**的写法，`StatementRegistry.match()` 找不到
     —— 窗口回你"已保存"，解析器当没这回事（卡片不出格子、表格不出列）；
  2. 插件（`extensions/*.json|*.py`）加的写法，同上；
  3. 「是否能够批处理」的产物是普通 dict，`plan_batch` 要 `CueRule`
     —— `AttributeError: 'dict' object has no attribute 'when'`。

外加这一轮补上的一件事：**代码格吃多词**（`Dissolve(0.1, 0.2)`）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from storygraph.app import batch_edit as BE
from storygraph.ext import registry as ER
from storygraph.ext import schema as ES
from storygraph.story import statement_registry as SR
from storygraph.story.spec_args import slot_value_map

SCRIPT = '''\
label start:
    show lan happy
    "第一句"
    mycue "闪电" "Dissolve(0.1, 0.2)" with Fade(0.2, 0, 0.2)
    "第二句"
'''

STATEMENT = {
    'id': 'mycue', 'kind': 'statement', 'label': '闪电动画', 'enabled': True,
    'fields': [
        {'key': '文字', 'kind': 'variable'},
        {'key': '片段', 'kind': 'code'},
        {'key': 'with', 'kind': 'keyword'},
        {'key': '转场', 'kind': 'expr'},
    ],
}

BATCH = {
    'id': 'master-dissolve', 'kind': 'batch', 'label': '主层过场',
    'enabled': True, 'batchable': True,
    'batch': {'when': {'show': 'sprite'},
              'insert': {'text': '$ renpy.transition(Dissolve(0.1), '
                                 'layer="master")',
                         'placement': 'after_last_show'}},
}


def _project(tmp_path: Path, ext=(STATEMENT, BATCH), script: str = SCRIPT) -> Path:
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    (root / 'game' / 'script.rpy').write_text(script, encoding='utf-8')
    (root / 'storygraph.assets.json').write_text(
        json.dumps({'characters': {'lan': '兰'}}), encoding='utf-8')
    if ext is not None:
        (root / ER.MANIFEST).write_text(json.dumps(
            {'schema': ES.EXT_SCHEMA, 'extensions': list(ext)},
            ensure_ascii=False), encoding='utf-8')
    return root


def _reg(root) -> SR.StatementRegistry:
    SR.clear_cache()
    return SR.StatementRegistry.for_project(str(root), use_cache=False)


# ----------------------------------------------------------------------
#  1. 新建的写法，解析器真的认得
# ----------------------------------------------------------------------

def test_a_statement_declared_only_in_ext_json_reaches_the_parser(tmp_path):
    """`ext.json` 里**只有**这一条（没有 rules.json）→ 注册表里必须有它。

    这是"在扩展窗口点新建"与"手写 rules.json"对工具应该是同一件事的判据。
    """
    root = _project(tmp_path)
    reg = _reg(root)
    spec = reg.match('mycue')
    assert spec is not None, '扩展窗口里新建的写法，解析器不认识'
    assert [(s.get('kind'), s.get('column') or s.get('text'))
            for s in spec.pattern] == [
        ('variable', '文字'), ('code', '片段'),
        ('keyword', 'with'), ('expr', '转场')]
    # 卡片 / 表格只出**取值格**（`with` 是写死的字，不该给人改）
    assert [c['name'] for c in SR.spec_columns(reg, 'mycue')] == [
        '文字', '片段', '转场']


def test_the_card_reads_the_values_of_the_new_statement(tmp_path):
    """读值走的是卡片那条真路（`slot_value_map`），不是另写一套。

    ⚠ 调用方先摘掉语句头（`beat_model._strip_head`），`pattern` 从**头之后**
      开始 —— 这里照同一口径写，免得测试自己发明一种调用方式。
    """
    root = _project(tmp_path)
    pattern = _reg(root).match('mycue').pattern
    values = slot_value_map(
        '"闪电" "Dissolve(0.1, 0.2)" with Fade(0.2, 0, 0.2)', pattern)
    assert values['文字'] == '闪电'
    assert values['片段'] == 'Dissolve(0.1, 0.2)'
    assert values['转场'] == 'Fade(0.2, 0, 0.2)'


def test_a_plugin_can_add_a_statement_and_says_where_it_came_from(tmp_path):
    """插件加的写法与项目自己声明的**待遇相同**，但来处要说得清。"""
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    (root / 'game' / 'script.rpy').write_text(SCRIPT, encoding='utf-8')
    (root / 'extensions').mkdir()
    (root / 'extensions' / '闪电动画.json').write_text(json.dumps(
        {'extensions': [STATEMENT]}, ensure_ascii=False), encoding='utf-8')

    spec = _reg(root).match('mycue')
    assert spec is not None and spec.pattern
    assert spec.origin == 'plugin:闪电动画.json'


def test_a_python_plugin_can_add_a_statement(tmp_path):
    """`.py` 插件（要执行项目代码的那一种）也要能加写法。"""
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    (root / 'game' / 'script.rpy').write_text(SCRIPT, encoding='utf-8')
    (root / 'extensions').mkdir()
    (root / 'extensions' / 'p.py').write_text(
        'def extensions():\n'
        '    return [{"id": "mycue", "kind": "statement",\n'
        '             "fields": [{"key": "文字", "kind": "variable"}]}]\n',
        encoding='utf-8')

    spec = _reg(root).match('mycue')
    assert spec is not None
    assert [s.get('column') for s in spec.pattern] == ['文字']


def test_a_call_declared_in_the_window_gets_real_columns(tmp_path):
    """`$ my_wipe(...)` 这类技术调用同样是"新建就生效"。

    字段 key 写成 `layer=` → 关键字参数；其余按**字段顺序**当位置参数
    （与 `parse_pattern` 里 `foo=` 的老约定一致，也是人最容易写对的形式）。
    """
    root = _project(tmp_path, ext=({'id': 'my_wipe', 'kind': 'call',
                                    'label': '擦除', 'enabled': True,
                                    'fields': [
                                        {'key': '方向', 'kind': 'variable'},
                                        {'key': 'layer=', 'kind': 'variable'},
                                    ]},))
    reg = _reg(root)
    spec = reg.call_spec('my_wipe')
    assert spec is not None
    assert [(c['name'], c['position'], c['kw']) for c in spec['columns']] == [
        ('方向', 0, ''), ('layer', None, 'layer')]
    cols = SR.call_columns(reg, 'my_wipe')
    assert [(c['name'], c['call']['kind']) for c in cols] == [
        ('方向', 'pos'), ('layer', 'kw')]


def test_the_extension_window_wins_over_the_old_manifest(tmp_path):
    """同一条写法：界面上改过的那份（ext.json）比手写 rules.json 更新。"""
    root = _project(tmp_path, ext=({
        'id': 'mycue', 'kind': 'statement', 'enabled': True,
        'fields': [{'key': '台词', 'kind': 'variable'}],
    },))
    (root / 'storygraph.rules.json').write_text(json.dumps({
        'statements': [{'name': 'mycue',
                        'pattern': [{'kind': 'variable', 'column': '旧列'}]}],
    }, ensure_ascii=False), encoding='utf-8')

    spec = _reg(root).match('mycue')
    assert [s.get('column') for s in spec.pattern] == ['台词']


def test_switching_a_new_statement_off_removes_it_from_the_tool(tmp_path):
    """「是否使用」对一个**新建**的写法同样有效（不只是旧清单里那些）。"""
    root = _project(tmp_path, ext=({**STATEMENT, 'enabled': False},))
    reg = _reg(root)
    assert reg.match('mycue') is None
    assert SR.spec_columns(reg, 'mycue') == []


# ----------------------------------------------------------------------
#  2. 字段词汇 = 解析器词汇（上一版这里会把整条扩展点丢掉）
# ----------------------------------------------------------------------

def test_the_words_alias_still_means_more_than_one_word():
    """`kind: "words"` = "这一格可以有多个词"。别名不能只改名字、丢掉这半句。

    两层都要对：字段层（`FieldSpec.multi`）与解析层（`pattern` 的 `multi`）——
    只对一层就会"界面说能写多个，解析器只吃一个"。
    """
    from storygraph.authoring.pattern import parse_pattern

    field = ES.parse_field({'key': '服装', 'kind': 'words'})
    assert field.kind == 'variable' and field.multi is True
    assert ES.slot_of_field(field)['multi'] is True
    slot = parse_pattern([{'kind': 'words', 'column': '服装'}])[0]
    assert (slot['kind'], slot['multi']) == ('variable', True)


def test_a_fixed_word_can_be_declared_and_is_not_dropped():
    """`kind: "keyword"` 以前会让**整条扩展点**被丢掉（只留一句 problems）。

    用户看到的现象是"我加的写法不见了"，而且没人告诉他发生了什么。
    """
    point = ES.parse_extension({
        'id': 'mycue', 'kind': 'statement',
        'fields': [{'key': '文字', 'kind': 'variable'},
                   {'key': 'with', 'kind': 'keyword'}],
    })
    assert [f.key for f in point.field_specs()] == ['文字', 'with']
    fixed = point.field('with')
    assert fixed.literal == 'with' and fixed.fixed is True
    assert fixed.widget == 'text' and fixed.parser_kind == 'keyword'


def test_the_field_vocabulary_is_the_parser_vocabulary():
    """一套词：`FIELD_KINDS` 必须覆盖解析器认的每一种格，且没有第二种叫法。"""
    from storygraph.authoring.pattern import SLOT_KINDS

    assert set(SLOT_KINDS) <= set(ES.FIELD_KINDS)
    assert set(ES.KIND_TO_SLOT.values()) <= set(SLOT_KINDS)
    # 别名字典只许出现在**同一处**（`KIND_ALIASES`），且都落到规范名上
    for alias, canon in ES.KIND_ALIASES.items():
        assert canon in ES.FIELD_KINDS and alias not in SLOT_KINDS


def test_slot_and_field_round_trip_keep_every_detail():
    """声明的一格 → 字段 → 声明：一个细节都不许在往返里掉。"""
    slot = {'kind': 'choice', 'column': '服装', 'options': ['泳装', '便服'],
            'multi': True, 'limit': True, 'group': 'outfit', 'required': False}
    field = ES.field_of_slot(slot)
    back = ES.slot_of_field(field)
    assert back['kind'] == 'choice'
    assert back['column'] == '服装'
    assert back['options'] == ['泳装', '便服']
    assert back['multi'] is True and back['limit'] is True
    assert back['group'] == 'outfit' and back['required'] is False


# ----------------------------------------------------------------------
#  3. 代码格吃多词（"下一件该做的事"）
# ----------------------------------------------------------------------

def _cells(pattern, line: str) -> dict:
    """一行 → 每格的值（词法用真的 `tokenize`，不是 `split`）。"""
    from storygraph.authoring.pattern import assign_tokens, tokenize

    body = line.strip()
    if body.startswith('mycue'):
        body = body[len('mycue'):].strip()
    slots = [s for s in pattern if isinstance(s, dict)]
    got = assign_tokens(tokenize(body, slots), slots)
    assert got['exact'] is True, got
    return {str(slots[i].get('column') or slots[i].get('text')): value
            for i, value in (got.get('values') or {}).items()}


@pytest.mark.parametrize('line', [
    'mycue "闪电" "Dissolve(0.1, 0.2)" with Fade(0.2, 0, 0.2)',   # 引号包起来
    'mycue 闪电 Dissolve(0.1, 0.2) with Fade(0.2, 0, 0.2)',       # 裸写
])
def test_a_code_cell_swallows_a_value_with_spaces(tmp_path, line):
    """`Dissolve(0.1, 0.2)` 是**一个值**，不是两个词。

    以前按空白切：代码格只拿到 `Dissolve(0.1,`，`0.2)` 变成"没人认领"
    —— 人写的是对的，工具说他切不上。
    """
    pattern = _reg(_project(tmp_path)).match('mycue').pattern
    assert _cells(pattern, line) == {
        '文字': '闪电', '片段': 'Dissolve(0.1, 0.2)',
        'with': 'with', '转场': 'Fade(0.2, 0, 0.2)'}


def test_a_code_cell_does_not_steal_a_candidate_word(tmp_path):
    """吃多词 ≠ 抢词：**别人写明的候选**，代码格不许吃掉。

    这是"自由文本"那条计价的另一半 —— 只把"不认识"降到 0，抢词（8）照旧贵，
    否则 `dissolve… 泳装` 里的 `泳装` 会被当成代码的一部分焊进去，服装那格
    空着，而人明明写了。
    """
    root = _project(tmp_path, ext=({
        'id': 'mycue', 'kind': 'statement', 'enabled': True,
        'fields': [{'key': '片段', 'kind': 'code'},
                   {'key': '服装', 'kind': 'choice',
                    'choices': ['泳装', '便服'], 'required': True}],
    },))
    pattern = _reg(root).match('mycue').pattern
    assert _cells(pattern, 'mycue Dissolve(0.1, 0.2) 泳装') == {
        '片段': 'Dissolve(0.1, 0.2)', '服装': '泳装'}


# ----------------------------------------------------------------------
#  4. 批处理开关 → 引擎真的能跑
# ----------------------------------------------------------------------

def test_the_batch_switch_produces_a_plan_the_engine_can_run(tmp_path):
    """`batchable` 的产物必须是引擎吃得下的东西（以前是 dict → AttributeError）。"""
    root = _project(tmp_path)
    rules, problems = BE.rules_from_extensions(str(root))
    assert problems == []
    assert [r.id for r in rules] == ['master-dissolve']

    plan = BE.plan_batch(str(root), rules)
    assert not plan.errors, plan.errors
    assert [fp.rel for fp in plan.files] == ['game/script.rpy']
    texts = [ins.text for fp in plan.files for ins in fp.insertions]
    assert texts == ['$ renpy.transition(Dissolve(0.1), layer="master")']


def test_the_batch_switch_off_means_no_rule_at_all(tmp_path):
    root = _project(tmp_path, ext=({**BATCH, 'batchable': False},))
    rules, problems = BE.rules_from_extensions(str(root))
    assert rules == [] and problems == []


def test_a_broken_batch_rule_is_reported_not_crashed(tmp_path):
    """坏规则记进 problems，**其余照常**（与插件装载同一条纪律）。"""
    bad = {**BATCH, 'id': 'bad', 'batch': {'insert': {'text': 'x',
                                                      'placement': '月球'}}}
    root = _project(tmp_path, ext=(BATCH, bad))
    rules, problems = BE.rules_from_extensions(str(root))
    assert [r.id for r in rules] == ['master-dissolve']
    assert problems and '月球' in problems[0]


def test_parse_rules_is_the_one_gate_into_the_engine(tmp_path):
    """原始 dict 必须过 `parse_rules`；直接喂 `plan_batch` 以前会炸。"""
    raw = ER.ExtensionRegistry.for_project(str(_project(tmp_path))).cue_rules()
    assert isinstance(raw[0], dict)                 # 扩展层产出的是纯数据
    parsed = BE.parse_rules(raw)
    assert isinstance(parsed[0], BE.CueRule)
    # 已经是规则对象的再喂一次也收（幂等）
    assert BE.parse_rules(parsed)[0] is parsed[0]


# ----------------------------------------------------------------------
#  6. 只改开关，不许有损覆盖旧清单的声明
# ----------------------------------------------------------------------

def test_switching_a_legacy_statement_never_rewrites_its_declaration(
        qapp, tmp_path):
    """在扩展窗口里停用一条旧清单写法 → 落盘的**只有开关**。

    为什么这条重要：旧清单那条投影是有损的（别名 / 块体 / 子句 / 渲染都在
    `pattern` → 字段 的转换里丢了）。如果把它存成一份新声明，用户看到的是
    "我啥也没干，别名和块体就没了"。所以存 `override: true`（只带开关）。
    """
    pytest.importorskip('PySide6')
    from storygraph.ui_qt.dialog_extensions import ExtensionDialog

    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    (root / 'game' / 'script.rpy').write_text(SCRIPT, encoding='utf-8')
    (root / 'storygraph.rules.json').write_text(json.dumps({
        'statements': [{'name': 'mycue cue', 'label': '闪电动画',
                        'block': True,
                        'pattern': [{'kind': 'variable', 'column': '文字'},
                                    {'kind': 'code', 'column': '片段'}],
                        'clauses': [{'keyword': 'with', 'name': '转场',
                                     'kind': 'transition'}]}],
    }, ensure_ascii=False), encoding='utf-8')

    dlg = ExtensionDialog(None, project_root=str(root))
    point = dlg._reg.get('mycue')
    dlg._load(point)
    assert not dlg.tbl.isEnabled(), '旧清单投影的字段表必须是只读的'
    dlg.ck_enabled.setChecked(False)
    dlg._save()
    dlg.close()

    saved = json.loads((root / ER.MANIFEST).read_text(encoding='utf-8'))
    entry = next(e for e in saved['extensions'] if e['id'] == 'mycue')
    assert entry.get('override') is True
    assert 'fields' not in entry, '只改开关，不该把字段表也存下去'

    # 关掉之后工具不认它
    assert _reg(root).match('mycue') is None

    # 再打开 → 仍然走旧清单那份声明：别名 / 块体 / 子句 / 两格 pattern 一个不少
    again = ER.ExtensionRegistry.for_project(root)
    shown = again.get('mycue')
    assert shown.override is True
    assert [f.key for f in shown.field_specs()] == ['文字', '片段', '转场']
    (root / ER.MANIFEST).write_text(json.dumps({
        'schema': ES.EXT_SCHEMA,
        'extensions': [{**entry, 'enabled': True}],
    }, ensure_ascii=False), encoding='utf-8')
    spec = _reg(root).match('mycue')
    assert [s.get('kind') for s in spec.pattern] == ['variable', 'code']
    assert [c.get('name') for c in (spec.clauses or ())] == ['转场']
    assert 'cue' in spec.aliases and spec.block == 'yes'


# ----------------------------------------------------------------------
#  5. 界面上的那一下：预演按钮真的算出了计划
# ----------------------------------------------------------------------

def test_the_preview_button_runs_the_engine(qapp, tmp_path):
    pytest.importorskip('PySide6')
    from storygraph.ui_qt.dialog_extensions import ExtensionDialog

    root = _project(tmp_path)
    dlg = ExtensionDialog(None, project_root=str(root))
    point = dlg._reg.get('master-dissolve')
    assert point is not None
    dlg._load(point)
    plan = dlg._preview_batch()
    assert plan is not None and not plan.errors
    shown = dlg.preview.toPlainText()
    assert 'renpy.transition' in shown
    dlg.close()


def test_the_push_button_writes_the_rule_into_the_batch_file(
        qapp, tmp_path, monkeypatch):
    """开关 → 规则文件：批处理窗口读的就是那份（这条链以前根本不存在）。

    顺带钉住纪律：**同 id 是替换不是追加**（规则 id 必须唯一），
    而且现有文件读不出来时**拒绝写入**（不许半读半改）。
    """
    pytest.importorskip('PySide6')
    from PySide6.QtWidgets import QMessageBox

    from storygraph.ui_qt.dialog_extensions import ExtensionDialog

    monkeypatch.setattr(QMessageBox, 'question',
                        lambda *a, **k: QMessageBox.Yes)
    root = _project(tmp_path)
    path = root / 'storygraph.batch.json'
    dlg = ExtensionDialog(None, project_root=str(root))
    dlg._load(dlg._reg.get('master-dissolve'))
    assert dlg._push_batch_rule() == path
    first = BE.load_rule_file(path)
    assert [r.id for r in first] == ['master-dissolve']

    # 再点一次：仍然是 1 条（同 id 就地更新，不会写出两条同名的）
    assert dlg._push_batch_rule() == path
    assert [r.id for r in BE.load_rule_file(path)] == ['master-dissolve']

    # 批处理窗口真的能看见它（"开关 → 规则文件 → 批处理窗口"这一条链的末端）
    from storygraph.ui_qt.dialog_batch import BatchDialog
    batch = BatchDialog(root)
    ids = [str((it.get('raw') or {}).get('id') or '') for it in batch._items]
    assert 'master-dissolve' in ids, ids
    batch.close()

    # 坏文件 → 拒绝写入，且**一个字节都不动**
    path.write_text('{ 不是 json', encoding='utf-8')
    assert dlg._push_batch_rule() is None
    assert path.read_text(encoding='utf-8') == '{ 不是 json'
    dlg.close()
