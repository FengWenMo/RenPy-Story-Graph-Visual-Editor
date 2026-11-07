# -*- coding: utf-8 -*-
"""**一条调用声明只有一个能改的地方**（2026-11-07 第四轮）

探针 `.pip-tmp/probe_call_owner.py` 的实测（这一轮开工前）：

    项目里 `storygraph.ext.json` 声明了 my_wipe（方向 / 秒数）
      → 在「技术调用」窗口改名字、点保存 → 返回 `ok: True`（"已保存 1 条"）
      → 重新装配：认到的**还是扩展那份**（改了个寂寞）
      → rules.json 里多了一份永远读不到的声明
      → 再把扩展那条关掉：rules.json 里那份**跟着消失**

这个文件钉三件事：

  1. **来处**（`origin`）：每条调用声明说得出自己写在哪（扩展 / 规则文件 /
     插件），不是一律"清单里来的"；
  2. **落点**（`save_calls` / 调用窗口）：写进**生效的那个文件**；写不进去的
     就说清为什么、去哪改，**绝不**回一句"已保存"；
  3. **参数表**：`columns` 那张小表（位置 / 关键字两格）与存盘形状一一对应，
     写错当场指出第几行。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from storygraph.ext import registry as ER
from storygraph.ext import schema as ES
from storygraph.story import statement_registry as SR

SCRIPT = '''\
label start:
    show lan happy
    $ my_wipe("left", 0.6)
    "台词"
'''

CALL_EXT = {
    'id': 'my_wipe', 'kind': 'call', 'label': '擦除过场', 'enabled': True,
    'fields': [
        {'key': '方向', 'label': '方向', 'kind': 'variable'},
        {'key': '秒数', 'label': '秒数', 'kind': 'number'},
    ],
}


def _project(tmp_path: Path, ext=(CALL_EXT,), rules_calls=None,
             plugin=None) -> Path:
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    (root / 'game' / 'script.rpy').write_text(SCRIPT, encoding='utf-8')
    if ext is not None:
        (root / ER.MANIFEST).write_text(json.dumps(
            {'schema': ES.EXT_SCHEMA, 'extensions': list(ext)},
            ensure_ascii=False), encoding='utf-8')
    if rules_calls is not None:
        (root / 'storygraph.rules.json').write_text(json.dumps(
            {'calls': rules_calls}, ensure_ascii=False), encoding='utf-8')
    if plugin is not None:
        (root / 'extensions').mkdir()
        (root / 'extensions' / '过场.json').write_text(json.dumps(
            {'extensions': list(plugin)}, ensure_ascii=False),
            encoding='utf-8')
    return root


def _reg(root) -> SR.StatementRegistry:
    SR.clear_cache()
    return SR.StatementRegistry.for_project(root)


def _rules(root) -> dict:
    path = root / 'storygraph.rules.json'
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding='utf-8') or '{}')


# ---------------------------------------------------------------------------
#  一、来处：这条声明写在哪
# ---------------------------------------------------------------------------

def test_an_extension_call_says_it_lives_in_ext_json(tmp_path):
    """扩展里声明的那条 → `origin` 是**文件名**，不是装载分类 `project`。

    为什么较真这个字符串：调用窗口和 `save_calls` 都要按它决定"写哪个文件"。
    写成 `project`（装载来源的分类），界面上一句"来处：project"人看不懂，
    而分流那边会认不出它归扩展管 —— 又回到"写错文件、改了不起作用"。
    """
    root = _project(tmp_path)
    spec = _reg(root).call_spec('my_wipe')
    assert spec['origin'] == 'storygraph.ext.json'
    assert SR.call_owner(spec['origin']) == 'ext'


def test_a_rules_json_call_says_rules_json(tmp_path):
    root = _project(tmp_path, ext=None, rules_calls=[{
        'head': 'my_wipe', 'columns': [{'name': '方向', 'position': 0}]}])
    spec = _reg(root).call_spec('my_wipe')
    assert spec['origin'] == 'storygraph.rules.json'
    assert SR.call_owner(spec['origin']) == 'rules'


def test_a_manifest_inside_game_still_counts_as_rules_json(tmp_path):
    """清单可以在 `game/` 下（`manifest_path` 两处都认）——按**文件名**认，
    按路径写死的话这类项目会被当成"来路不明"。"""
    root = _project(tmp_path, ext=None, rules_calls=None)
    (root / 'game' / 'storygraph.rules.json').write_text(json.dumps({
        'calls': [{'head': 'my_wipe',
                   'columns': [{'name': '方向', 'position': 0}]}]}),
        encoding='utf-8')
    spec = _reg(root).call_spec('my_wipe')
    assert spec['origin'] == 'storygraph.rules.json'
    assert SR.call_owner(spec['origin']) == 'rules'


def test_a_plugin_call_says_which_plugin_file(tmp_path):
    root = _project(tmp_path, ext=None, plugin=({
        'id': 'my_wipe', 'kind': 'call',
        'fields': [{'key': '方向', 'kind': 'variable'}]},))
    spec = _reg(root).call_spec('my_wipe')
    assert spec['origin'] == 'plugin:过场.json'
    assert SR.call_owner(spec['origin']) == 'plugin'


def test_call_owner_only_trusts_the_file_name():
    """四个落点 + 一个兜底（没有 origin = 老数据，只有规则文件这一条路）。"""
    assert SR.call_owner('') == 'rules'
    assert SR.call_owner('storygraph.rules.json') == 'rules'
    assert SR.call_owner('game/storygraph.rules.json') == 'rules'
    assert SR.call_owner('storygraph.ext.json') == 'ext'
    assert SR.call_owner('plugin:闪电动画.json') == 'plugin'
    assert SR.call_owner('builtin') == 'builtin'
    # 认不出来的一律当规则文件 —— 那是 2026-11-07 之前**唯一**的写入口，
    # 猜成扩展会让人"改了没反应"，猜成规则文件最多是多写一份看得见的。
    assert SR.call_owner('某个奇怪的东西') == 'rules'


# ---------------------------------------------------------------------------
#  二、落点：写进生效的那个文件
# ---------------------------------------------------------------------------

def test_saving_an_extension_owned_call_is_refused_not_faked(tmp_path):
    """**这一轮的主判据**：扩展管的那条，保存要说"没写"，不能回"已保存"。

    探针里最气人的就是这里：`declare_calls` 返回 `ok: True`，界面照念，
    而重新装配后一个字都没变。
    """
    root = _project(tmp_path)
    spec = _reg(root).call_spec('my_wipe')
    res = SR.save_calls(root, [{
        'head': 'my_wipe', 'label': '我改的名字',
        'columns': [{'name': '我的新参数', 'position': 0}],
        'origin': spec['origin']}])

    assert res['ok'] is True                 # 不是"出错"，是"这里轮不到它改"
    assert res['written'] == []
    assert [b['head'] for b in res['blocked']] == ['my_wipe']
    assert res['blocked'][0]['owner'] == 'ext'
    # 规则文件里**一个字都没写**（连空文件都不建 —— 写盘要有理由）
    assert not (root / 'storygraph.rules.json').exists()
    # 重新装配：还是扩展那份（不是"被我改坏了"，也不是"被盖住了"）
    spec2 = _reg(root).call_spec('my_wipe')
    assert spec2['label'] == '擦除过场'
    assert [c['name'] for c in spec2['columns']] == ['方向', '秒数']


def test_a_rules_owned_call_still_saves_normally(tmp_path):
    """普通项目（没有扩展）这条路**一点没变** —— 别为了修扩展把老路修坏。"""
    root = _project(tmp_path, ext=None, rules_calls=[{
        'head': 'my_wipe', 'label': '旧名',
        'columns': [{'name': '方向', 'position': 0}]}])
    res = SR.save_calls(root, [{
        'head': 'my_wipe', 'label': '新名',
        'columns': [{'name': '方向', 'position': 0},
                    {'name': 'layer', 'kw': 'layer'}]}])

    assert res['ok'] is True
    assert res['written'] == ['my_wipe']
    assert res['blocked'] == []
    spec = _reg(root).call_spec('my_wipe')
    assert spec['label'] == '新名'
    assert [(c['name'], c['kw']) for c in spec['columns']] == [
        ('方向', ''), ('layer', 'layer')]
    # `origin` 是"来处"，不进文件（写回来会被当成声明内容）
    assert 'origin' not in json.dumps(_rules(root), ensure_ascii=False)


def test_saving_only_blocked_calls_does_not_create_a_file(tmp_path):
    root = _project(tmp_path)
    res = SR.save_calls(root, [{
        'head': 'my_wipe', 'columns': [{'name': '方向', 'position': 0}],
        'origin': 'storygraph.ext.json'}])
    assert res['ok'] is True and res['written'] == []
    assert not (root / 'storygraph.rules.json').exists()


def test_a_shadowed_entry_left_in_rules_json_is_cleaned_up(tmp_path):
    """以前误写进去的那份（被扩展盖住的）顺手清掉 —— 它读不到，还会在
    "扩展一关"的时候跟着消失（探针④），留着只会骗下一个人。"""
    root = _project(tmp_path, rules_calls=[{
        'head': 'my_wipe', 'label': '以前写错的',
        'columns': [{'name': '旧参数', 'position': 0}]}])
    res = SR.save_calls(root, [{
        'head': 'my_wipe', 'columns': [{'name': '方向', 'position': 0}],
        'origin': 'storygraph.ext.json'}])

    assert res['written'] == []
    assert res['deleted'] == ['my_wipe']
    assert _rules(root).get('calls') == []
    assert _reg(root).call_spec('my_wipe')['label'] == '擦除过场'


def test_a_plugin_owned_call_is_blocked_too(tmp_path):
    """插件文件是**输入**（可能还是别人的）：就地改会被下次加载冲掉。"""
    root = _project(tmp_path, ext=None, plugin=({
        'id': 'my_wipe', 'kind': 'call',
        'fields': [{'key': '方向', 'kind': 'variable'}]},))
    res = SR.save_calls(root, [{
        'head': 'my_wipe', 'columns': [{'name': '方向', 'position': 0}],
        'origin': 'plugin:过场.json'}])
    assert res['written'] == []
    assert res['blocked'][0]['owner'] == 'plugin'
    assert not (root / 'storygraph.rules.json').exists()


def test_save_calls_without_a_project_root_says_so():
    res = SR.save_calls(None, [{'head': 'my_wipe',
                                'columns': [{'name': 'x', 'position': 0}]}])
    assert res['ok'] is False and '没有项目根' in res['reason']


def test_keyword_rows_do_not_shift_positions():
    """关键字那几行**不占位置**：`layer=` 写在第一行也不该把「方向」挤到第 1 个。

    从前用的是字段下标当 position，于是这种排法会让工具去读 `args[1]` ——
    而人明明写的是第一个位置参数。错得**不报错**，只是读错值。
    """
    from storygraph.ext.bridge import call_declarations
    from storygraph.ext.schema import parse_extension

    point = parse_extension({
        'id': 'my_wipe', 'kind': 'call',
        'fields': [{'key': 'layer=', 'kind': 'variable'},
                   {'key': '方向', 'kind': 'variable'},
                   {'key': '秒数', 'kind': 'number'}]})
    calls = call_declarations([point])
    assert [(c['name'], c.get('position'), c.get('kw'))
            for c in calls[0]['columns']] == [
        ('layer', None, 'layer'), ('方向', 0, None), ('秒数', 1, None)]


# ---------------------------------------------------------------------------
#  三、参数表：位置 / 关键字两格，与存盘形状一一对应
# ---------------------------------------------------------------------------

def _params(qapp, columns=(), head='my_wipe'):
    from storygraph.ui_qt.call_params import CallParamsDialog
    return CallParamsDialog(columns, head=head)


def test_the_param_table_reads_the_columns_shape_directly(qapp):
    dlg = _params(qapp, [{'name': '方向', 'label': '往哪边', 'position': 0},
                         {'name': '层', 'kw': 'layer'}])
    assert dlg.table.rowCount() == 2
    assert dlg.table.item(0, 0).text() == '方向'
    assert dlg.table.item(0, 1).text() == '往哪边'
    assert dlg.table.item(0, 2).text() == '0'
    assert dlg.table.item(0, 3).text() == ''
    assert dlg.table.item(1, 2).text() == ''      # 关键字那行位置空着
    assert dlg.table.item(1, 3).text() == 'layer'
    columns, bad = dlg.built()
    assert bad == []
    assert columns == [{'name': '方向', 'label': '往哪边', 'position': 0},
                       {'name': '层', 'label': '层', 'kw': 'layer'}]


def test_a_new_positional_row_gets_the_next_free_number(qapp):
    dlg = _params(qapp, [{'name': '方向', 'position': 0},
                         {'name': '秒数', 'position': 1},
                         {'name': '层', 'kw': 'layer'}])
    dlg.add_positional()
    assert dlg.table.item(3, 2).text() == '2'
    dlg.table.item(3, 0).setText('额外')
    assert [c.get('position') for c in dlg.built()[0]] == [0, 1, None, 2]


@pytest.mark.parametrize('row, expect', [
    # (名字, 标签, 位置, 关键字) → 报错里必须说清**第几行**
    (('', '', '0', ''), '第 1 行没有参数名'),
    (('方向', '', '0', 'layer'), '只能选一个'),
    (('方向', '', 'x', ''), '不是数字'),
    (('方向', '', '', ''), '没填位置'),
    (('方向', '', '', '2bad'), '不是合法名字'),
])
def test_a_bad_row_says_which_row(qapp, row, expect):
    from PySide6.QtWidgets import QTableWidgetItem
    dlg = _params(qapp)
    dlg.table.insertRow(0)
    for col, text in enumerate(row):
        dlg.table.setItem(0, col, QTableWidgetItem(text))
    columns, bad = dlg.built()
    assert columns == []
    assert any(expect in text for text in bad), bad


def test_two_rows_cannot_claim_the_same_position(qapp):
    dlg = _params(qapp)
    for name in ('方向', '秒数'):
        dlg.table.insertRow(dlg.table.rowCount())
        from PySide6.QtWidgets import QTableWidgetItem
        row = dlg.table.rowCount() - 1
        dlg.table.setItem(row, 0, QTableWidgetItem(name))
        dlg.table.setItem(row, 2, QTableWidgetItem('0'))
    _columns, bad = dlg.built()
    assert any('一个位置只能有一格' in text for text in bad), bad


def test_an_empty_param_table_is_refused_rather_than_saving_nothing(qapp):
    dlg = _params(qapp)
    _columns, bad = dlg.built()
    assert any('至少要一格' in text for text in bad), bad


def test_the_param_dialog_stays_open_when_a_row_is_wrong(qapp):
    """有问题就留在窗口里说（关掉窗口会让人以为存了 —— 这一条的教训来自
    批处理窗口那几轮："按钮点得下去、什么也没发生"最伤人）。"""
    from PySide6.QtWidgets import QDialog, QTableWidgetItem
    dlg = _params(qapp)
    dlg.table.insertRow(0)
    dlg.table.setItem(0, 0, QTableWidgetItem('方向'))
    dlg.table.setItem(0, 2, QTableWidgetItem('0'))
    dlg.table.setItem(0, 3, QTableWidgetItem('layer'))
    dlg._accept()
    assert dlg.result() != QDialog.Accepted
    assert '只能选一个' in dlg.status.text()


# ---------------------------------------------------------------------------
#  四、窗口：看得见"归谁"，改不了的说清去哪改
# ---------------------------------------------------------------------------

def _dialog(qapp, root):
    from storygraph.ui_qt.dialog_calls import CallDialog
    return CallDialog(None, project_root=root)


def test_the_window_shows_the_summary_and_who_owns_each_row(qapp, tmp_path):
    from PySide6.QtCore import Qt
    root = _project(tmp_path, rules_calls=[{
        'head': 'film_transition', 'label': '胶片',
        'columns': [{'name': '转场', 'position': 0}]}])
    dlg = _dialog(qapp, root)
    assert dlg.table.rowCount() == 2
    owners = {dlg.table.item(r, 0).text(): dlg.table.item(r, 5).text()
              for r in range(dlg.table.rowCount())}
    assert owners == {'my_wipe': '扩展', 'film_transition': '规则文件'}
    # 参数那一格是**摘要**（只读），不是要人背的语法
    row = [r for r in range(2)
           if dlg.table.item(r, 0).text() == 'my_wipe'][0]
    assert dlg.table.item(row, 4).text() == '方向@0; 秒数@1'
    assert not (dlg.table.item(row, 4).flags() & Qt.ItemIsEditable)


def test_rows_owned_elsewhere_are_read_only(qapp, tmp_path):
    """归扩展管的那一行，格子**点不动** —— 让人改了才发现没生效是最坏的。"""
    from PySide6.QtCore import Qt
    root = _project(tmp_path)
    dlg = _dialog(qapp, root)
    assert not (dlg.table.item(0, 1).flags() & Qt.ItemIsEditable), \
        '中文名不该能改'
    assert '别处' in dlg.table.item(0, 1).toolTip()


def test_editing_params_of_a_rules_owned_row_updates_the_summary(qapp,
                                                                 tmp_path):
    root = _project(tmp_path, ext=None, rules_calls=[{
        'head': 'my_wipe', 'columns': [{'name': '方向', 'position': 0}]}])
    dlg = _dialog(qapp, root)
    # 直接走编辑器落地那一步（`edit_params` 里 exec 之后调的就是它；
    # 这里不开模态窗口 —— 模态在判据里会挂住）
    dlg._set_columns(0, [{'name': '方向', 'position': 0},
                         {'name': '层', 'kw': 'layer'}], '')
    assert dlg.table.item(0, 4).text() == '方向@0; 层=layer'
    calls = dlg.built_calls()
    assert [(c['name'], c.get('kw')) for c in calls[0]['columns']] == [
        ('方向', None), ('层', 'layer')]


def test_asking_to_edit_an_extension_row_points_at_the_ext_window(qapp,
                                                                 tmp_path):
    root = _project(tmp_path)
    dlg = _dialog(qapp, root)
    assert dlg.edit_params(0) is None
    assert '扩展' in dlg.status.text() and '参数形状' in dlg.status.text()


def test_saving_from_the_window_reports_both_halves(qapp, tmp_path):
    """一屏两行（一条扩展、一条规则）→ 存完之后：写了的说写了、没写的说为什么。"""
    root = _project(tmp_path, rules_calls=[{
        'head': 'film_transition',
        'columns': [{'name': '转场', 'position': 0}]}])
    dlg = _dialog(qapp, root)
    row = [r for r in range(dlg.table.rowCount())
           if dlg.table.item(r, 0).text() == 'film_transition'][0]
    dlg.table.item(row, 1).setText('胶片过场')
    res = dlg._save()

    assert res['written'] == ['film_transition']
    assert [b['head'] for b in res['blocked']] == ['my_wipe']
    assert dlg.changed is True
    assert '已保存 1 条' in dlg.status.text()
    assert 'my_wipe' in dlg.status.text() and '扩展' in dlg.status.text()
    # 规则文件里只有规则文件那条，扩展那条一个字没动
    heads = [c['head'] for c in _rules(root).get('calls') or ()]
    assert heads == ['film_transition']
    assert _reg(root).call_spec('film_transition')['label'] == '胶片过场'
    assert _reg(root).call_spec('my_wipe')['label'] == '擦除过场'


def test_saving_only_extension_rows_writes_nothing(qapp, tmp_path):
    root = _project(tmp_path)
    dlg = _dialog(qapp, root)
    res = dlg._save()
    assert res['written'] == [] and res['deleted'] == []
    assert dlg.changed is False
    assert not (root / 'storygraph.rules.json').exists()
    assert '扩展' in dlg.status.text()


def test_the_extension_window_can_be_focused_on_one_point(qapp, tmp_path):
    """从调用窗口"送过去"要**落在那一条上**（不然让人在四组树里自己找）。"""
    from storygraph.ui_qt.dialog_extensions import ExtensionDialog
    root = _project(tmp_path)
    dlg = ExtensionDialog(None, project_root=root, focus_id='my_wipe')
    assert dlg._current is not None
    assert dlg._current.id == 'my_wipe'
    assert dlg.focus_on('不存在的id') is False


def test_the_extension_window_says_it_changed_after_saving(qapp, tmp_path):
    from storygraph.ui_qt.dialog_extensions import ExtensionDialog
    root = _project(tmp_path)
    dlg = ExtensionDialog(None, project_root=root)
    assert dlg.changed is False
    dlg._save()
    assert dlg.changed is True


# ---------------------------------------------------------------------------
#  五、同一条规则也管语句：`dialog_learn` 不往会被盖住的那份文件里写
# ---------------------------------------------------------------------------

STATEMENT_EXT = {
    'id': 'mycue', 'kind': 'statement', 'label': '闪电动画', 'enabled': True,
    'fields': [{'key': '文字', 'kind': 'variable'}],
}


def _learn_dialog(root):
    from storygraph.ui_qt.dialog_learn import CustomStatementDialog
    SR.clear_cache()
    return CustomStatementDialog(None, project_root=root, discovered=[])


def test_statement_owner_treats_a_cds_declaration_as_writable():
    """脚本里 `renpy.register_statement` 那几条**可以**在这里教 —— 写进
    rules.json 会覆盖 CDS（`declared` 盖 `host`），那正是教它认人的方式。"""
    assert SR.statement_owner('ch01.rpy:12') == 'rules'
    assert SR.statement_owner('') == 'rules'
    assert SR.statement_owner('storygraph.ext.json') == 'ext'
    assert SR.statement_owner('plugin:闪电动画.json') == 'plugin'


def test_an_extension_statement_cannot_be_saved_from_the_learn_dialog(
        qapp, tmp_path):
    """扩展里声明过的写法：在这儿保存会写进 rules.json，而它**读不到**。

    同一个坑在调用那边已经用探针实测过（`probe_call_owner.py`）——语句这边
    是同一个装配顺序（扩展 > rules.json），所以同一条纪律：不写、说清去哪。
    """
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    (root / ER.MANIFEST).write_text(json.dumps(
        {'schema': ES.EXT_SCHEMA, 'extensions': [STATEMENT_EXT]},
        ensure_ascii=False), encoding='utf-8')
    SR.clear_cache()
    decl = SR.StatementRegistry.for_project(root).match('mycue')
    assert decl is not None and decl.origin == 'storygraph.ext.json'

    dlg = _learn_dialog(root)
    dlg._load_statement(decl)
    dlg.label.setText('闪电（改过）')
    assert dlg._save_statement() is False
    assert '扩展' in dlg._status.text()
    assert not (root / 'storygraph.rules.json').exists()
    # 重新装配：一个字没变（不是"悄悄写了一份看不见的"）
    decl2 = SR.StatementRegistry.for_project(root).match('mycue')
    assert decl2.label == '闪电动画'


def test_a_rules_statement_still_saves_from_the_learn_dialog(qapp, tmp_path):
    """没有扩展的项目这条路**一点没变**（别为了修新坑把老路修坏）。"""
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    (root / 'storygraph.rules.json').write_text(json.dumps({
        'statements': [{'name': 'mycue', 'label': '旧名',
                        'columns': [{'name': '文字', 'label': '文字'}],
                        'pattern': [{'kind': 'variable', 'column': '文字'}]}],
    }, ensure_ascii=False), encoding='utf-8')
    SR.clear_cache()
    decl = SR.StatementRegistry.for_project(root).match('mycue')
    dlg = _learn_dialog(root)
    dlg._load_statement(decl)
    dlg.label.setText('新名')
    assert dlg._save_statement() is True, dlg._status.text()
    data = json.loads((root / 'storygraph.rules.json').read_text(
        encoding='utf-8'))
    assert data['statements'][0]['label'] == '新名'
