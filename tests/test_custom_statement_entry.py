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


def test_params_are_cards_not_a_cramped_table(qapp, tmp_path):
    """
    ⭐ 用户 2026-10-11 的实测反馈就是判据：

    > "表格太小了！多个参数还要下拉看……而且编剧仍然看不明白"

    钉住三件事：
      ① 参数区**不再是表格**（是竖排卡片列表）；
      ② **不出现横向滚动**（多个参数只会往下排）；
      ③ 候选值**不是逗号串**，是能点的小方块（编剧不用记分隔符）。
    """
    from PySide6.QtWidgets import QTableWidget
    from PySide6.QtCore import Qt
    from storygraph.ui_qt.dialog_learn import CustomStatementDialog
    from storygraph.ui_qt.param_cards import ParamList

    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    dlg = CustomStatementDialog(None, project_root=root, discovered=[])
    assert isinstance(dlg.params, ParamList), '参数区不该再是表格'
    assert not isinstance(dlg.params, QTableWidget)
    assert dlg.params.horizontalScrollBarPolicy() == Qt.ScrollBarAlwaysOff, \
        '多个参数只能往下排，绝不能要人横着拉'

    # 加三个参数（多参数才是用户抱怨的场景）→ 三张卡，仍然不横向滚
    dlg.params.clear()
    for name, label, choices in (('intensity', '强度', '1,2,3'),
                                 ('expression', '表情', 'happy,shy'),
                                 ('who', '对谁说', 'lyj')):
        dlg.params.add_param(name, label, choices)
    assert dlg.params.count() == 3
    rows = dlg.params.rows()
    assert [r['name'] for r in rows] == ['intensity', 'expression', 'who']
    # 候选值进去出来是同形（方块 ↔ 逗号串）
    assert rows[0]['choices'] == '1,2,3', rows[0]
    assert not dlg.params.horizontalScrollBar().isVisible()


def test_group_section_is_collapsed_by_default(qapp, tmp_path):
    """
    词组是**高级功能**，默认收起（用户："多个参数还要下拉看"）——
    收起后纵向空间全给参数卡；已有声明照样填进去，勾开就能看到。
    """
    from storygraph.ui_qt.dialog_learn import CustomStatementDialog

    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    dlg = CustomStatementDialog(None, project_root=root, discovered=[])
    assert dlg.groups_box.isCheckable()
    assert not dlg.groups_box.isChecked(), '默认该是收起的'
    assert not dlg.groups.isVisible(), '收起时词组列表不该占空间'
    # 已有声明仍然填着（勾开就能看到，不会丢）
    dlg.groups.clear()
    dlg.groups.add_group('outfit', '服装', 'clothes2')
    assert dlg.groups.count() == 1
    dlg.groups_box.setChecked(True)
    assert dlg.groups.count() == 1, '勾开之后声明还在'


def test_plain_text_import_goes_through_the_session_entry(ui_window):
    """
    ⭐⭐ **S3/S4 缺的那一环**（2026-10-11 发现）：`EditorSession.import_plain_text`
    早就实现了（含 `.docx`、含"未确认角色就一个字都不写"、决定写回 characters.xlsx），
    **只是界面从没调它** —— 我第一版还在界面里自己写了一份转换器，那是**重复实现**。

    这条钉住：`.txt/.docx` 一律走**会话那条正式链路**，界面不自己造一套。
    """
    win = ui_window
    called = {}
    sess = win._session

    class _Stop(Exception):
        """哨兵：记完调用就中止 —— 免得去满足导入后处理的一堆字段契约。"""

    def fake_plain(files, graph=None, *, ask=None, **kw):
        called['files'] = list(files)
        called['has_ask'] = callable(ask)
        called['ask_result'] = ask([{'token': 'MOM'}]) if ask else None
        return {'ok': False, 'needs_decision': True, 'written': [],
                'messages': ['要确认 MOM']}

    def fake_chapters(paths, graph=None):
        called['chapters'] = list(paths)
        raise _Stop

    sess.import_plain_text = fake_plain
    sess.import_chapters = fake_chapters
    win._log = lambda *_a, **_k: None
    try:
        win.import_chapter_files(['a.txt', 'b.docx', 'c.rpy'])
    except _Stop:
        pass
    assert called.get('files') == ['a.txt', 'b.docx'], called
    assert called.get('chapters') == ['c.rpy'], '纯文字之外的才走 .rpy 那条'
    assert called.get('has_ask') is True, '没把"问一句"的回调给会话'
    # ⚠ 回调返回 None = **这次不决定**（会话保证不落盘）—— 界面不许替人猜
    assert called.get('ask_result') is None


def test_groups_are_attached_to_the_slot_that_needs_them(qapp, tmp_path):
    """
    ⭐⭐ **用户卡点 5 原话**：
    > "「词组（可选·高级）」是什么鬼：我作为程序员，猜这可能是正则表达式的
    >  捕获组，或者是枚举的分类。但我完全没有输入正则表达式的地方！
    >  如果我要用 swim_suit 来匹配 泳装，我该写在哪？"

    答：词组**不是**独立表格 —— 它是"**这一格的候选值从哪来**"。
    所以：① 建了词组，格子的下拉里就能选到；② 选了之后，试切会用这个词组的候选值；
    ③ 删/加词组，格子上的下拉**跟着变**。
    """
    from storygraph.ui_qt.dialog_learn import CustomStatementDialog
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    dlg = CustomStatementDialog(None, project_root=root, discovered=[])
    # ① 建一个词组「服装」，候选值：泳装 / 校服
    dlg.groups.clear()
    dlg.groups.add_group('outfit', '服装', '泳装,校服')
    dlg._sync_groups_to_slots()
    # ② 那一格（枚举）的下拉里能选到它
    dlg.params.clear()
    dlg.params.add_param('泳装', '服装', '泳装,校服', kind='choice')
    card = dlg.params._cards[0]
    combo = card._group
    assert combo.findData('outfit') >= 0, '格子上选不到刚建的那个词组'
    combo.setCurrentIndex(combo.findData('outfit'))
    # ③ 交出去的序列里带着这个词组 → 试切/落进声明都能用上
    slots = dlg.params.slots()
    assert slots[0]['group'] == 'outfit', slots
    assert slots[0]['kind'] == 'choice'
    # ④ 删掉词组 → 格子上的下拉跟着变（但**当前值不许丢**）
    dlg.groups.clear()
    dlg._sync_groups_to_slots()
    assert dlg.params.slots()[0]['group'] == 'outfit', '切一下就白填了'


def test_the_dialog_answers_whether_show_overrides_the_engine(qapp, tmp_path):
    """
    ⭐⭐ **用户卡点 1 的原话**：

    > "我第一反应是填 show。但是等一下，show 是 Ren'Py 的原生指令！
    >  如果我把 show 注册成自定义语句，会不会覆盖引擎原有的 show？
    >  工具会怎么处理这个冲突？"

    界面必须**当场回答**，而不是让人卡死。真机核过的答案是：
    `rules.json` 里的声明**只影响工具**（表格列、下拉），**不碰引擎**；
    真正替换引擎的是项目里的 `renpy.register_statement(...)`（CDS 覆盖内置 → N5 警告）。
    """
    from storygraph.ui_qt.dialog_learn import CustomStatementDialog
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    dlg = CustomStatementDialog(None, project_root=root, discovered=[])
    # 默认是"我们自己写的"（老行为不变）
    assert dlg.statement_mode() == 'custom'
    dlg.mode.setCurrentIndex(dlg.mode.findData('builtin'))
    assert dlg.statement_mode() == 'builtin'
    note = dlg.mode_note.text()
    assert '不会动引擎' in note, note
    assert 'register_statement' in note, note
    # 选了"引擎自带"→ 从**下拉里挑**（不让人猜该填 show 还是 show 角色 服装）
    # ⚠ 用 `isHidden()`：窗口没 show 时 `isVisible()` 恒为 False（判据自己的坑）
    assert not dlg.builtin_name.isHidden(), '选了引擎自带却没露出下拉'
    assert dlg.name.isHidden(), '两个输入框不该同时露着'
    assert dlg.builtin_name.count() > 0, '引擎自带的写法列表是空的'
    dlg.builtin_name.setCurrentText('show')
    assert dlg.name.text() == 'show', '挑了下拉却没填进语句名'


def test_builtin_mode_lists_real_engine_heads_not_a_hardcoded_sheet(qapp,
                                                                   tmp_path):
    """下拉要**问引擎**（`engine_facts` 的注册表）；读不到才退回工具词表。"""
    from storygraph.ui_qt.dialog_learn import CustomStatementDialog
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    dlg = CustomStatementDialog(None, project_root=root, discovered=[])
    heads = [dlg.builtin_name.itemText(i)
             for i in range(dlg.builtin_name.count())]
    assert heads, '一个都没有'
    # 工具词表兜底时也必须有 show / say 这类基本写法
    assert 'show' in heads or 'say' in heads, heads[:12]


def test_the_dialog_can_try_cutting_a_real_line(qapp, tmp_path):
    """
    ⭐⭐ **回答用户卡死的第一步**（程序员撞墙记录）：
    "语句名该填什么、这一行会被怎么切" —— 粘一行真实剧本，当场看到切法。

    验三件：① 切得对时显示每格+列名；② 顺序不对时**明确说切不上**；
    ③ 每格有**类型**可选（变量 vs 枚举由人选，不靠猜）。
    """
    from storygraph.ui_qt.dialog_learn import CustomStatementDialog
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    dlg = CustomStatementDialog(None, project_root=root, discovered=[])
    dlg.params.clear()
    dlg.params.add_param('show', '', '', kind='keyword')
    dlg.params.add_param('小明', '角色', '', kind='variable')
    dlg.params.add_param('泳装', '服装', '泳装,校服', kind='choice')
    dlg.params.add_param('开心', '神态', '开心,害羞', kind='choice')
    dlg.sample.setText('show 小明 泳装 开心')
    dlg._refresh_cut()
    text = dlg.cut_view.text()
    assert '切出来是这样' in text, text
    assert '角色' in text and '服装' in text and '神态' in text, text
    assert '4 格' in text, text

    # 顺序不对（把"服装/神态"两格换位置）→ **明确说切不上**，不是默默不动
    slots = dlg.params.slots()
    slots[2], slots[3] = slots[3], slots[2]
    from storygraph.authoring import pattern as P
    got = P.match_line(slots, 'show 小明 泳装 开心')
    assert got['ok'] is False and got['reason']

    # 没粘样例时要有**引导**（不是空白）
    dlg.sample.clear()
    dlg._refresh_cut()
    assert '粘一行' in dlg.cut_view.text(), dlg.cut_view.text()


def test_each_slot_has_a_kind_and_can_be_reordered(qapp, tmp_path):
    """
    用户卡点：① 凭什么知道 `小明` 是变量、`泳装` 是枚举 → **格子自己标类型**；
    ② 竖排的三张卡凭什么对应那个顺序 → **顺序能调，而且确实生效**。
    """
    from storygraph.ui_qt.dialog_learn import CustomStatementDialog
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    dlg = CustomStatementDialog(None, project_root=root, discovered=[])
    dlg.params.clear()
    dlg.params.add_param('小明', '角色', '', kind='variable')
    dlg.params.add_param('泳装', '服装', '泳装', kind='choice')
    rows = dlg.params.rows()
    assert [r['kind'] for r in rows] == ['variable', 'choice'], rows
    # 调顺序：第二格上移 → 顺序真的变了
    card = dlg.params._cards[1]
    dlg.params.move(card, -1)
    assert [r['label'] for r in dlg.params.rows()] == ['服装', '角色']
    # 到边界不出错（第一格再上移 = 什么都不做）
    dlg.params.move(dlg.params._cards[0], -1)
    assert [r['label'] for r in dlg.params.rows()] == ['服装', '角色']


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


def test_no_markdown_markers_in_widget_text(qapp, tmp_path):
    """
    ⭐ 规矩 N6 的自动判据：**Qt 不渲染 Markdown** ——
    界面上出现 `**加粗**` 只会显示成一堆星号（我自己就犯过：
    验证按钮的说明里带 `**两把锁**`，截图一看满屏星号）。

    扫整个弹窗里所有控件的可见文字，发现 `**` 就报出来。
    （反引号放宽：它在界面上读起来像"强调一个标识符"，无害；
    `**` 则一定是 Markdown 习惯漏出来的。）
    """
    import re
    from PySide6.QtWidgets import QLabel, QPushButton, QCheckBox
    from storygraph.ui_qt.dialog_learn import CustomStatementDialog

    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    dlg = CustomStatementDialog(None, project_root=root, discovered=[],
                                on_verify=lambda head: 'ok')
    bad = []
    widgets = (dlg.findChildren(QLabel) + dlg.findChildren(QPushButton)
               + dlg.findChildren(QCheckBox))       # findChildren 一次只收一个类型
    for w in widgets:
        texts = [w.text() if hasattr(w, 'text') else '',
                 w.toolTip() if hasattr(w, 'toolTip') else '']
        for t in texts:
            if not t:
                continue
            if '**' in t:
                bad.append(f'{type(w).__name__}: {t[:60]}')
    assert not bad, ('Qt 不渲染 Markdown，这些文字会原样显示成星号：'
                     + ' | '.join(bad))


def test_no_markdown_markers_in_any_string_literal():
    """
    ⭐ 上面那条只扫"当前能显示出来的那段文字" —— 会**漏**掉暂时不显示的
    （例如"没发现未声明的写法"那句，只有发现为空时才渲染，我截图里正好没显示，
    判据就放过了它）。所以这里做一层**源码级**的兜底：
    扫 `dialog_learn.py` 里所有字符串字面量，出现 `**` 就报。

    （反引号不放宽在这一层：源码里反引号是给 Markdown 文档用的，
    界面文字里出现它一定是习惯漏出来的 —— 但界面**确实有用反引号强调标识符**的，
    所以只钉 `**`，反引号留给人工看。）
    """
    import ast
    src = (ROOT / 'storygraph' / 'ui_qt' / 'dialog_learn.py').read_text(
        encoding='utf-8')
    tree = ast.parse(src)
    # ⚠ 先排掉 **docstring** —— 那是给读代码的人看的（Markdown 是对的），
    #   只有"会显示到界面上的字符串"才不许带 `**`
    #   （我第一版没排，于是把模块/函数文档全报出来了 —— 判据自己太宽）。
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, 'body', None) or []
            if body and isinstance(body[0], ast.Expr) \
                    and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                docstrings.add(id(body[0].value))
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and id(node) not in docstrings:
            if '**' in node.value:
                bad.append(f'第 {node.lineno} 行: {node.value[:50]}')
    assert not bad, ('Qt 不渲染 Markdown；这些字符串里的 `**` 会原样显示：'
                     + ' | '.join(bad))


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
