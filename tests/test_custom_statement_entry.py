"""
自定义模块入口判据（发现 → 填写 → 声明 → 立刻生效）
=======================================================

用户 2026-10-10 报的缺口：

> 你说支持自适应的解析……但是我并没有找到一个入口可以教这个工具学会
> 我自定义的项目模块……至于什么 `swim_suit` 一类的貌似无法做到呢……
> 至少我想要个能够自定义的模块入口放在最上方

后端一直是齐的（`declare_statement()` 是"教一次"的唯一写入口，
`tests/test_learn_statement.py` 钉着它，CLI `learn` 在用它），缺的是
**发现 + 入口**。这个文件钉住新加的那两半：

  ① `statement_registry.discover_undeclared()` —— 扫项目找出"还不认识的写法"
     （**判据照抄 `coverage_of_statements` 的口径**：`raw_kind` 在
     `Statement.payload` 里，不是 beat 属性 —— 我第一版就是按 beat 属性取，
     恒为空 → 一个候选都发现不了）；
  ② `authoring.learn_statement.build_declaration()` —— 表单 → 后端参数
     （**表单数据最脏**：`intensity=3`、中文名、重复项……都要**出声**处理，
     因为后端对坏条目是静默跳过的）；
  ③ 弹窗（offscreen 真 Qt）+ 工具栏入口在**最上面**；
  ④ **声明完立刻生效**：T3 → T1a、参数成为列、候选值成为下拉，而**产物一字不变**。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.authoring.learn_statement import (                    # noqa: E402
    build_declaration, declaration_preview, parse_choices)
from storygraph.story import statement_registry as SR                 # noqa: E402

SRC = '''label ch01:
    swim_suit lyj "泳装" intensity=3
    lyj "第一句"
    mycue "闪电" intensity=2:
        lyj "块里的台词"
    if love > 3:
        swim_suit lyj "泳装2"
    show lyj happy
    return
'''


@pytest.fixture
def project(tmp_path):
    """一个最小项目（有 game/ → `discover_scripts` 认得出）。"""
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    (root / 'game' / 'ch.rpy').write_text(SRC, encoding='utf-8')
    SR.clear_cache()
    yield root
    SR.clear_cache()


# ==================== ① 发现 ====================

def test_discovery_lists_the_undeclared_heads(project):
    found = {r['head']: r for r in SR.discover_undeclared(project)}
    assert set(found) == {'swim_suit', 'mycue'}, found
    assert found['swim_suit']['count'] == 2
    assert found['mycue']['block'] is True
    assert found['swim_suit']['block'] is False
    assert found['swim_suit']['samples'], '要带原文样例（人凭它认得出）'


def test_discovery_is_sorted_by_how_often_it_is_used(project):
    heads = [r['head'] for r in SR.discover_undeclared(project)]
    assert heads[0] == 'swim_suit', heads          # 用了 2 次，排最前


def test_discovery_skips_builtin_statements(project):
    """`show` / `if` / `return` 这些**认识的**东西不许出现在候选里。"""
    heads = {r['head'] for r in SR.discover_undeclared(project)}
    assert 'show' not in heads and 'if' not in heads and 'return' not in heads


def test_discovery_stops_listing_once_declared(project):
    assert [r['head'] for r in SR.discover_undeclared(project)] == \
        ['swim_suit', 'mycue']
    res = SR.declare_statement(project, 'swim_suit')
    assert res['ok'], res
    SR.clear_cache()
    assert [r['head'] for r in SR.discover_undeclared(project)] == ['mycue']


def test_discovery_also_sees_cds_declarations(project):
    """脚本里用 `renpy.register_statement` 声明过的也算"认识"（别重复提示）。"""
    (project / 'game' / 'cds.rpy').write_text(
        'init python:\n'
        '    renpy.register_statement("swim_suit", parse=renpy.parser.'
        'parse_statement)\n', encoding='utf-8')
    SR.clear_cache()
    heads = {r['head'] for r in SR.discover_undeclared(project)}
    assert 'swim_suit' not in heads, heads


def test_discovery_needs_a_real_project(tmp_path):
    assert SR.discover_undeclared(None) == []
    assert SR.discover_undeclared(tmp_path / 'nope') == []


# ==================== ② 表单 → 后端参数（纯函数） ====================

@pytest.mark.parametrize('text,want', [('1,2,3', ['1', '2', '3']),
                                       ('1，2、3', ['1', '2', '3']),
                                       ('1;2|3', ['1', '2', '3']),
                                       ('低 中 高', ['低', '中', '高'])])
def test_choices_accept_every_common_separator(text, want):
    assert parse_choices(text) == want


def test_choices_do_not_split_when_there_is_no_separator():
    """只写一个值就当一个值 —— 绝不擅自切（`spec_arg_choices` 那条契约）。"""
    assert parse_choices('intensity') == ['intensity']
    assert parse_choices('') == []


def test_build_declaration_tolerates_an_equals_sign():
    """用户顺手写 `intensity=3`：按 `intensity` 处理，并**报出来**。"""
    built = build_declaration('swim_suit', params=[{'name': 'intensity=3'}])
    assert built['ok'] and built['columns'] == [{'name': 'intensity'}]
    assert any('=' in w for w in built['warnings'])


def test_build_declaration_reports_bad_parameter_names():
    """
    ⚠ 后端对坏条目是**静默跳过**的 → 这里必须出声，否则用户以为加上了，
    而表里根本没有那一列。
    """
    built = build_declaration('swim_suit',
                              params=[{'name': '泳装'}, {'name': 'ok_name'}])
    assert [c['name'] for c in built['columns']] == ['ok_name']
    assert any('泳装' in w for w in built['warnings'])


def test_build_declaration_dedupes_and_reports():
    built = build_declaration('swim_suit',
                              params=[{'name': 'a'}, {'name': 'a'}])
    assert [c['name'] for c in built['columns']] == ['a']
    assert any('两次' in w for w in built['warnings'])


def test_build_declaration_refuses_a_bad_statement_name():
    assert not build_declaration('9bad')['ok']
    assert not build_declaration('')['ok']
    assert build_declaration('mycue cue')['ok'], '多词写法合法（后端拆成别名）'


def test_build_declaration_keeps_block_unknown_by_default():
    """没勾"带块"就**不写** `block` —— 后端默认 `possible`（不确定就别写死）。"""
    assert build_declaration('x')['block'] is None
    assert build_declaration('x', block=True)['block'] is True


def test_the_preview_says_what_will_be_written():
    text = declaration_preview(build_declaration(
        'swim_suit', label='泳装切换', docs='切到泳装',
        params=[{'name': 'intensity', 'choices': '1,2,3'}]), path='p.json')
    for want in ('swim_suit', '泳装切换', 'intensity', '1，2，3', 'p.json'):
        assert want in text, text


# ==================== ③ 弹窗与入口（offscreen 真 Qt） ====================

def test_the_toolbar_entry_is_the_first_action(ui_window):
    """
    用户要的是"入口放在**最上方**" —— 所以判据是**工具栏里的第一个动作**
    （不是"全窗口第一个 QAction"：菜单/快捷键那些更早建出来）。
    """
    tb = ui_window._tb_graph
    texts = [a.text() for a in tb.actions() if a.text().strip()]
    assert texts, texts
    assert '自定义模块' in texts[0], texts[:3]
    # 而且它排在「导入章节」**前面**（这是"最上方"的实际含义）
    assert texts.index([t for t in texts if '自定义模块' in t][0]) \
        < texts.index([t for t in texts if '导入章节' in t][0])


def test_the_dialog_prefills_from_a_discovered_head(qapp, project):
    from storygraph.ui_qt.dialog_learn import CustomStatementDialog
    discovered = SR.discover_undeclared(project)
    dlg = CustomStatementDialog(None, project_root=project,
                                discovered=discovered,
                                registry_report='（还没声明）')
    assert dlg.found.rowCount() == len(discovered)
    dlg.found.setCurrentCell(0, 0)                     # 点第一条
    assert dlg.name.text() == 'swim_suit'
    built = dlg.result_declaration()
    assert built['ok'] and built['name'] == 'swim_suit'


def test_the_toolbar_action_actually_opens_the_dialog(ui_window, monkeypatch):
    """
    ⚠⚠ **用户实测报的 bug**："打开项目之后这个按钮无法点击 / 点了没反应"。

    根因：那条链里把**资产注册表**（`session.registry()` → `AssetRegistry`）
    当成**语句注册表**喂给了 `format_registry_report` →
    `TypeError: 'AssetRegistry' object is not iterable` →
    **异常在 Qt 槽里被吞掉**，表现就是"点了毫无反应"。

    为什么以前没抓到：我只有一条"工具栏里有这个动作"的存在性判据 ——
      **存在 ≠ 能跑**。这条判据真的 `trigger()` 一次，并且检查控制台里
     **不许出现**"没写/失败/TypeError"这类字样（Qt 槽吞掉的异常在测试里
      也看不出来，所以同时钉住"链上不许报错"）。
    """
    from PySide6.QtGui import QAction
    from storygraph.ui_qt import dialog_learn

    opened = []

    class _FakeDialog:
        # ⚠ `**kw` 刻意留着：以后弹窗加参数（比如 `declared_groups`）时，
        # 这条判据应该继续测"动作能不能跑通"，而不是因为签名变了就红。
        def __init__(self, parent=None, project_root=None, declares=None,
                     discovered=None, registry_report='', **kw):
            opened.append({'root': project_root, 'discovered': discovered,
                           'report': registry_report, 'kw': kw})

        def exec(self):
            return 0                       # 等于"用户取消"

    monkeypatch.setattr(dialog_learn, 'CustomStatementDialog', _FakeDialog)
    logged = []
    monkeypatch.setattr(ui_window, '_log', lambda m: logged.append(str(m)))

    action = next(a for a in ui_window._tb_graph.actions()
                  if '自定义模块' in a.text())
    assert isinstance(action, QAction)
    action.trigger()                       # **真的点**

    assert opened, '点了没打开弹窗（用户报的那个 bug）'
    assert opened[0]['report'] and 'iterable' not in opened[0]['report']
    assert all(('失败' not in m and '没写' not in m and 'Traceback' not in m)
               for m in logged), logged
    assert any('扫出' in m for m in logged), logged


def test_the_dialog_disables_writing_until_the_name_is_valid(qapp, project):
    from storygraph.ui_qt.dialog_learn import CustomStatementDialog
    dlg = CustomStatementDialog(None, project_root=project, discovered=[])
    assert not dlg._ok_btn.isEnabled(), '空名字时不该能按下去'
    dlg.name.setText('swim_suit')
    assert dlg._ok_btn.isEnabled()
    dlg.name.setText('9 bad')
    assert not dlg._ok_btn.isEnabled()


def test_the_dialog_carries_params_into_the_declaration(qapp, project):
    from storygraph.ui_qt.dialog_learn import CustomStatementDialog
    dlg = CustomStatementDialog(None, project_root=project, discovered=[])
    dlg.name.setText('swim_suit')
    dlg.block.setChecked(True)
    dlg.params.add_param('intensity', '强度', '1,2,3')
    built = dlg.result_declaration()
    assert built['block'] is True
    assert built['columns'] == [{'name': 'intensity', 'label': '强度',
                                'choices': ['1', '2', '3']}]


def test_the_dialog_itself_writes_nothing(qapp, project):
    """弹窗只出参数；写盘是 `declare_statement` 的事。"""
    from storygraph.ui_qt.dialog_learn import CustomStatementDialog
    before = sorted(p.name for p in project.iterdir())
    dlg = CustomStatementDialog(None, project_root=project,
                                discovered=SR.discover_undeclared(project))
    dlg.found.setCurrentCell(0, 0)
    dlg.result_declaration()
    assert sorted(p.name for p in project.iterdir()) == before
    assert not (project / 'storygraph.rules.json').exists()


# ==================== ④ 声明完**立刻生效** ====================

def _capability_of_swim_suit(project):
    from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
    pr, _ = IncrementalRenPyAnalyzer().analyze(
        files=[project / 'game' / 'ch.rpy'], graph=None)
    reg = SR.registry_for_parse_result(pr)
    caps = {c.head: c for c in SR.coverage_of_statements(
        pr.statements, registry=reg).samples}
    return caps.get('swim_suit') or SR.capability_of('swim_suit', reg), reg


def test_declaring_makes_the_tool_understand_it(project):
    """
    用户真正想要的那一步：教一次之后 `swim_suit` 从"工具完全不知道它是什么"
    变成"项目声明过、并给了参数形状"（T1a）→ 参数成为列、候选值成为下拉。
    """
    before, reg0 = _capability_of_swim_suit(project)
    assert before.level == 'T3', before
    assert SR.arg_columns(reg0, 'swim_suit') == []

    built = build_declaration('swim_suit', label='泳装切换',
                              params=[{'name': 'intensity', 'label': '强度',
                                       'choices': ['1', '2', '3']}])
    res = SR.declare_statement(project, built['name'], label=built['label'],
                               columns=built['columns'], docs=built['docs'])
    assert res['ok'] and res['action'] == 'added', res
    SR.clear_cache()

    after, reg1 = _capability_of_swim_suit(project)
    assert after.level == 'T1a', after
    assert after.provenance == 'declared'
    assert [c['name'] for c in SR.arg_columns(reg1, 'swim_suit')] == ['intensity']
    assert SR.spec_arg_choices(reg1, 'swim_suit', 'intensity') == ['1', '2', '3']


def test_declaring_does_not_change_the_product(project):
    """声明只影响"工具怎么理解"，**渲染产物一个字不变**。"""
    from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
    from storygraph.story.beat_build import beats_from_parse_result
    from storygraph.story.beat_render import render_beats_to_rpy

    def product():
        pr, _ = IncrementalRenPyAnalyzer().analyze(
            files=[project / 'game' / 'ch.rpy'], graph=None)
        return render_beats_to_rpy(beats_from_parse_result(pr))

    before = product()
    SR.declare_statement(project, 'swim_suit', columns=[{'name': 'intensity'}])
    SR.clear_cache()
    after = product()
    assert after == before
    assert '    swim_suit lyj "泳装" intensity=3' in after


def test_the_manifest_is_human_readable_and_keeps_other_keys(project):
    """清单是**给人评审**的：缩进 JSON、中文不转义、其它键原样保留。"""
    (project / 'storygraph.rules.json').write_text(json.dumps(
        {'statements': [], '我的备注': '别弄丢我'}, ensure_ascii=False),
        encoding='utf-8')
    SR.declare_statement(project, 'swim_suit', docs='切到泳装')
    data = json.loads((project / 'storygraph.rules.json').read_text(
        encoding='utf-8'))
    assert data['我的备注'] == '别弄丢我'
    assert [s['name'] for s in data['statements']] == ['swim_suit']
    raw = (project / 'storygraph.rules.json').read_text(encoding='utf-8')
    assert '\\u' not in raw, raw


def test_a_broken_manifest_is_refused_not_overwritten(project):
    """清单坏了 → **拒绝写**（绝不拿我们这份覆盖用户手写的东西）。"""
    bad = project / 'storygraph.rules.json'
    bad.write_text('{ 这不是 JSON', encoding='utf-8')
    res = SR.declare_statement(project, 'swim_suit')
    assert not res['ok'] and 'JSON' in res['reason']
    assert bad.read_text(encoding='utf-8') == '{ 这不是 JSON'


# ==================== ⑤ "声明完在卡片里能编辑参数"（用户要的"编辑服装"） ====================

def _beats_of(project):
    from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
    from storygraph.story.beat_build import beats_from_parse_result
    pr, _ = IncrementalRenPyAnalyzer().analyze(
        files=[project / 'game' / 'ch.rpy'], graph=None)
    reg = SR.registry_for_parse_result(pr)
    SR.annotate_spec_statements(pr.statements, reg)
    return beats_from_parse_result(pr), reg


def test_the_card_editor_gets_the_declared_params(project):
    """
    用户："卡片编辑器内有没有自定义模块的显示？如果没有的话，那我还是无法编辑
    服装啊？" —— **有**，但前提是**先声明**（没声明时它只当代码行躺着）。

    卡片那一组控件的语义在 `app.beats.spec_arg_fields_for` /
    `spec_arg_choices_for`（与表格列**同源**），这里按卡片实际拿到的数据断言。

    ⚠ 两种情形都要钉（我第一版把两者搞混，断言当场红）：
      · 头行里**写了**的参数 → 控件里就是它的值；
      · 头行里**没写**的参数 → 空格子（`认不出就不猜`），但**列还在** ——
        人可以在卡片里把它填上，填完就折回头行。
    """
    from storygraph.app import beats as _beats

    SR.declare_statement(project, 'swim_suit', label='泳装切换',
                         columns=[{'name': 'pose', 'label': '姿势',
                                   'choices': ['happy', 'calm', 'sad']},
                                  {'name': 'intensity', 'label': '强度',
                                   'choices': ['1', '2', '3']}],
                         docs='把角色切到泳装')
    SR.clear_cache()
    beats_, _reg = _beats_of(project)
    stmts = SR.StatementRegistry.for_project(str(project))
    target = next(b for b in beats_
                  if getattr(b, 'spec_name', '') == 'swim_suit')

    cols, values = _beats.spec_arg_fields_for(target, stmts)
    headers = [c['header'] for c in cols]
    assert headers == ['swim_suit·pose', 'swim_suit·intensity'], headers
    # fixture 那一行是 `swim_suit lyj "泳装" intensity=3`（没写 pose）
    assert values == {'swim_suit·pose': '',
                      'swim_suit·intensity': '3'}, values
    choices = _beats.spec_arg_choices_for(target, stmts)
    assert choices['pose'] == ['happy', 'calm', 'sad'], choices
    assert choices['intensity'] == ['1', '2', '3'], choices

    # 卡片里把 pose 填成 happy + intensity 改成 9 → 折回头行
    from storygraph.story.beat_model import fold_spec_arg_columns
    fold_spec_arg_columns(target, {'swim_suit·pose': 'happy',
                                   'swim_suit·intensity': '9'})
    head = str(getattr(target, 'spec_head', '') or getattr(target, 'opaque', ''))
    assert 'pose=happy' in head and 'intensity=9' in head, head


def test_changing_a_param_changes_the_product(project):
    """在卡片/表格里把 `intensity` 改成 9 → 产物那一行跟着变成 `intensity=9`。"""
    from storygraph.story.beat_model import fold_spec_arg_columns
    from storygraph.story.beat_render import render_beats_to_rpy

    SR.declare_statement(project, 'swim_suit',
                         columns=[{'name': 'intensity'}])
    SR.clear_cache()
    beats_, _reg = _beats_of(project)
    target = next(b for b in beats_
                  if getattr(b, 'spec_name', '') == 'swim_suit')
    fold_spec_arg_columns(target, {'swim_suit·intensity': '9'})
    out = render_beats_to_rpy(beats_)
    assert '    swim_suit lyj "泳装" intensity=9' in out
    assert 'intensity=3' not in out


def test_the_statement_registry_is_not_the_asset_registry(ui_window):
    """
    ⚠⚠ 用户实测报的 bug（"按钮点了没反应"）的根因，**钉成判据防复发**：

    `EditorSession.registry()` 给的是**资产注册表**（`AssetRegistry`），
    而语句声明要的是 **`StatementRegistry`**。两都叫 "registry"，
    传错了症状是 `TypeError` 在 Qt 槽里被吞掉 → 用户看到"点了毫无反应"。
    """
    from storygraph.story.statement_registry import StatementRegistry

    asset_reg = ui_window._session.registry()
    stmt_reg = StatementRegistry.for_project(str(ui_window._project_root))
    assert not isinstance(asset_reg, StatementRegistry), (
        '如果哪天 session.registry() 变成语句注册表，这条判据要跟着改 —— '
        '但现在它确实不是，所以**不能**拿它喂 format_registry_report')
    assert isinstance(stmt_reg, StatementRegistry)
    # 报告函数只吃后者
    assert 'iterable' not in SR.format_registry_report(stmt_reg)


def test_the_verify_button_calls_back_and_can_be_disabled(qapp, tmp_path):
    """
    ⭐ 「▶ 验证它能跑」（第三层落地）：
      · **没注入回调就灰化**（借用户的教训：界面必须能干活，能点就必须有反应）；
      · 有回调时点了会调它，并把结论显示在预览区；
      · 回调抛异常也不许炸窗口（验证是"可失败的动作"）。
    """
    from storygraph.ui_qt.dialog_learn import CustomStatementDialog

    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    # ① 没回调 → 按钮灰化（不是"点下去什么都不发生"）
    dlg0 = CustomStatementDialog(None, project_root=root, discovered=[])
    assert not dlg0._verify_btn.isEnabled()

    # ② 有回调 → 点了会调，结论显示出来
    seen = []

    def fake(head):
        seen.append(head)
        return '✅ 通过：引擎报 PASSED，且证据文件确实存在'

    dlg = CustomStatementDialog(None, project_root=root, discovered=[],
                                on_verify=fake)
    assert dlg._verify_btn.isEnabled()
    dlg.name.setText('weather')
    dlg._do_verify()
    assert seen == ['weather'], seen
    assert '通过' in dlg.preview.toPlainText(), dlg.preview.toPlainText()

    # ③ 没填动作名 → 提示，而不是默默不动
    dlg.name.setText('')
    dlg._do_verify()
    assert '先填' in dlg.preview.toPlainText(), dlg.preview.toPlainText()

    # ④ 回调抛异常 → 显示失败原因，不炸
    def boom(head):
        raise RuntimeError('引擎没了')

    dlg2 = CustomStatementDialog(None, project_root=root, discovered=[],
                                 on_verify=boom)
    dlg2.name.setText('weather')
    dlg2._do_verify()
    assert '引擎没了' in dlg2.preview.toPlainText()
    assert dlg2._verify_btn.isEnabled(), '跑完要恢复可点'


def test_every_panel_method_the_window_calls_really_exists(ui_window):
    """
    ⚠ 这条是"卡死"那个 bug 的守卫（2026-10-10 实测踩到）：

    窗口只持有**面板**（`BeatCardEditor`），网格在它里面 —— 所以每个网格方法
    都要在面板上**转一手**。我漏了 `set_choice_groups_provider` →
    `'BeatCardEditor' object has no attribute …` → 异常落进 `load_script` 的
    `except` → **`QMessageBox.critical` 模态弹窗** → 测试挂死、用户看到"卡住"。

    做法：静态扫窗口源码里 `self._panel.xxx(` 的调用，逐个断言面板上真的有。
    比"再多写一条用例"可靠 —— 漏的方法本来就不会被任何用例覆盖到。
    """
    import re
    from pathlib import Path

    panel = ui_window._panel
    src = ''
    pkg = Path(ROOT) / 'storygraph' / 'ui_qt'
    for f in sorted(pkg.glob('window_*.py')) + [pkg / 'main_window.py']:
        src += f.read_text(encoding='utf-8')
    called = sorted(set(re.findall(r'self\._panel\.([A-Za-z_]\w*)\s*\(', src)))
    assert called, '没扫到任何 self._panel.* 调用（判据本身失效了）'
    missing = [m for m in called if not hasattr(panel, m)]
    assert not missing, f'窗口调了面板上不存在的方法（会变成模态弹窗卡死）：{missing}'
