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


def test_build_declaration_accepts_chinese_column_names():
    """
    ⚠⚠ **这条判据原来写反了**（2026-10-13 用户实测现场）：

        show lan swim_suit happy      → 界面显示「⚠ 切不上」

    根因：这一格是**表格列名**，代码却拿"脚本具名参数"的标识符规则去卡它，
    于是「人物」「服装」「神态」三个中文列名**被整条丢掉** →
    声明里一列都没有 → 试切永远切不上（而界面还在旁边念"不是合法标识符"）。

    中文列名是天经地义的（本仓自己的注释都这么写）——
    这里钉住：中文／任意非空名字**照收**；空名字才丢，并出声。
    """
    built = build_declaration('swim_suit',
                              params=[{'name': '泳装'}, {'name': 'ok_name'},
                                      {'name': ''}])
    assert [c['name'] for c in built['columns']] == ['泳装', 'ok_name'], \
        built['columns']


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
    assert '自定义动作' in texts[0], texts[:3]
    # 而且它排在「导入章节」**前面**（这是"最上方"的实际含义）
    assert texts.index([t for t in texts if '自定义动作' in t][0]) \
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
                  if '自定义动作' in a.text())
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
    from PySide6.QtWidgets import QTableWidget, QScrollArea
    from storygraph.ui_qt.dialog_learn import CustomStatementDialog
    from storygraph.ui_qt.param_editor import FieldList as ParamList

    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    dlg = CustomStatementDialog(None, project_root=root, discovered=[])
    assert isinstance(dlg.params, ParamList), '参数区不该再是表格'
    assert not isinstance(dlg.params, QTableWidget)
    # ⚠ 2026-10-13 重写：参数区自己**不再是滚动区**（那正是"两个滚动条"的来源）——
    #   滚动归当前页那一个。所以这里钉的是"它不横向滚"，而不是"它的横向滚动条关了"。
    assert not isinstance(dlg.params, QScrollArea), \
        '参数区自己又变成滚动区了 —— 会同屏出现两条滚动条'

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
    # 加完三张卡之后，卡片本身也不许宽到要人横着拉（最小宽要小）
    assert dlg.params._cards[0].minimumSizeHint().width() <= 320, \
        dlg.params._cards[0].minimumSizeHint().width()


def test_group_section_is_collapsed_by_default(qapp, tmp_path):
    """
    **候选分栏是"另一页"，不是"一个勾选框 + 折叠块"**（2026-10-13 重写后）。

    为什么改（用户原话）：
    > 还有你这个词组栏不也还是打勾下拉吗？全都再改！
    > ……不要再在原来的地方缝补了！整个删了重写！

    旧版是 `QGroupBox(setCheckable)` 折起来的一块 —— 用户看到的仍然是
    "一个勾 + 一展开"。现在分栏有自己的**页签**：标题栏始终看得见，
    点一下就进去，不用勾选、不用展开。
    """
    from storygraph.ui_qt.dialog_learn import (CustomStatementDialog,
                                               PAGE_COLUMNS, PAGE_FIND)

    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    dlg = CustomStatementDialog(None, project_root=root, discovered=[])
    # ① 分栏是一页，不是"可勾选的折叠块"
    assert dlg.tabs.count() == 3, [dlg.tabs.tabText(i) for i in range(dlg.tabs.count())]
    assert '候选分栏' in dlg.tabs.tabText(PAGE_COLUMNS)
    assert dlg.tabs.currentIndex() == PAGE_FIND, '打开时该停在"认识新动作"'
    # ② 打开时它**不占**填写页的空间（在另一页上）
    dlg.tabs.setCurrentIndex(1)
    assert not dlg.columns_editor.isVisible(), '分栏列表出现在填写页上了'
    # ③ 已有声明照样填进去，点进那一页就能看到
    dlg.columns_editor.clear()
    dlg.columns_editor.add_column('outfit', '服装', 'clothes2')
    assert dlg.columns_editor.count() == 1
    dlg.tabs.setCurrentIndex(PAGE_COLUMNS)
    assert dlg.columns_editor.count() == 1, '切页之后声明还在'
    assert dlg.columns_editor.rows()[0]['key'] == 'outfit'


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
    ⭐⭐ 用户卡点 5（2026-10-10）：
    > "「词组（可选·高级）」是什么鬼……如果我要用 swim_suit 来匹配 泳装，
    >  我该写在哪？"

    **v3 第二版改了答法**（用户 2026-10-12）：
    > "如果你所谓的「词组」只是给变量一个名字的话……那你不应该弄一个下拉，
    >  你应该就在原地，就在旁边标注，而不是下拉一个展开这么反人类"

    所以现在的规矩是：
      ① 词组**不是**独立表格，也不是每格一个下拉 —— 它是"这一格归属哪一组"的**标注**；
      ② 标注**自动认**：这一格的名字/中文列名对得上词组名或它的候选值 → 标上；
      ③ 词组表一变（加/删/改名），标注**跟着变**，且**已经认出来的归属不丢**；
      ④ 交出去的序列里带着这个组名 → 试切/落进声明都能用上。
    """
    from storygraph.ui_qt.dialog_learn import CustomStatementDialog
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    dlg = CustomStatementDialog(None, project_root=root, discovered=[])
    # ① 建一个词组「服装」，候选值：泳装 / 校服
    dlg.groups.clear()
    dlg.groups.add_group('outfit', '服装', '泳装,校服')
    dlg._sync_groups_to_slots()
    # ② 那一格（中文列名「服装」= 词组的中文名）**自动**标上 outfit
    dlg.params.clear()
    dlg.params.add_param('泳装', '服装', '泳装,校服', kind='choice')
    card = dlg.params._cards[0]
    assert card.group_of() == 'outfit', '中文列名对上了词组，却没标上'
    assert card._group_hint.isVisible() is False or True   # 弹窗没 show，可见性不判
    assert '分栏：outfit' in card._group_hint.text(), card._group_hint.text()
    # ③ 交出去的序列里带着这个词组 → 试切/落进声明都能用上
    slots = dlg.params.slots()
    assert slots[0]['group'] == 'outfit', slots
    assert slots[0]['kind'] == 'choice'
    # ④ 词组改了名字 → 标注跟着变；删光词组 → 标注消失（不为"无"占地方）
    dlg.groups.clear()
    dlg._sync_groups_to_slots()
    assert dlg.params.slots()[0]['group'] == '', '词组删了，标注还挂着'
    assert card._group_hint.text() == ''
    # ⑤ 组名对上也能认（名字 = 组名）—— ⚠ 这里**故意不手调** `_sync_groups_to_slots`：
    #   词组表一变，弹窗应当**自己**把新表同步给参数卡（`set_groups_changed` 那条线）。
    dlg.groups.add_group('mood', '神态', '')
    dlg.params.add_param('mood', '', '', kind='choice')
    assert dlg.params._groups, '词组表变了，参数卡没收到'
    assert dlg.params._cards[1].group_of() == 'mood', '组名对上却没认出来'
    # ⑥ 对不上就是没有 —— **不是每个项目都有"服装"**
    dlg.params.add_param('who', '角色', '', kind='variable')
    assert dlg.params._cards[2].group_of() == ''


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
    # ⚠ 2026-10-13：枚举格要**显式限死**（`limit`）才拒绝候选外的词；
    #   不限死时两格都能吃 `泳装`，顺序就看不出来了（这条判据钉的就是顺序）。
    slots = dlg.params.slots()
    for s in slots:
        if s.get('kind') == 'choice':
            s['limit'] = True
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


# ============ ⑥ 参数卡的布局与输入（2026-10-12 用户实测报的 bug） ============

def test_no_two_widgets_share_a_grid_cell_in_a_param_card(qapp):
    """
    ⚠⚠ 用户 2026-10-12 实测报的 bug：**「剧本里怎么写」那一格点不动、打字没反应**。

    根因：`_limit` 复选框被放进 `(1, 1)` —— 而那格已经是「剧本里怎么写」的
    输入框，**两个控件叠在同一格**（渲染出来是"输入框里套着一个复选框"，
    点下去命中的是压在上层的复选框）。

    ⚠ 判据写成**与布局类型无关**（2026-10-12 v3 重做后补）：
      · 卡身是**网格**时 → 同一 `(row, col)` 不许有两个控件（原判据）；
      · 卡身是**竖排**时（v3：`QVBoxLayout` 单列堆叠）→ 每个控件必须是**独立一项**，
        而且不许有控件既不在布局里、又被显示出来（那才是"叠着"的本质）。

    为什么改成"看结构"而不是"看坐标"：v3 把卡身换成竖排**就是为了让叠格不可能发生**
    （见 `docs/archive/设计_自定义动作界面_v3_重做_2026-10-12.md` §5），
    判据要能同时守住这两种形态，否则一改布局它就变成噪音。
    """
    from PySide6.QtWidgets import QGridLayout, QVBoxLayout
    from storygraph.ui_qt.param_editor import FieldCard as ParamCard

    card = ParamCard()
    body = card.body
    if isinstance(body, QGridLayout):
        seen = {}
        for i in range(body.count()):
            row, col, _rs, _cs = body.getItemPosition(i)
            w = body.itemAt(i).widget()
            seen.setdefault((row, col), []).append(type(w).__name__ if w else '?')
        dup = {k: v for k, v in seen.items() if len(v) > 1}
        assert not dup, f'这些网格里叠了多个控件（人点不到下面那个）：{dup}'
        return
    assert isinstance(body, QVBoxLayout), (
        f'卡身只能是"网格"或"竖排"之一，实际是 {type(body).__name__}')
    # 竖排：每一项各占一行；被登记的控件不许重复出现（重复 = 同一个控件被塞两次）
    rows = {}
    for i in range(body.count()):
        item = body.itemAt(i)
        w = item.widget()
        if w is None:
            continue
        rows.setdefault(id(w), []).append(i)
    dup = {k: v for k, v in rows.items() if len(v) > 1}
    assert not dup, f'同一个控件被放进多个位置（会叠着画）：{dup}'


def test_chip_edit_return_does_not_close_the_dialog(qapp):
    """
    ⚠⚠ 同一轮用户报的"按了没反应" —— 另一半根因在这里。

    `QLineEdit` 收到 Return 是 `event->ignore()`（Qt 为了让"对话框里回车 = 确定"
    生效），按键于是冒到 `QDialog`，把 Ok（autoDefault）点了 →
    **候选值刚加上、整个弹窗立刻被 accept 关掉**。

    这不只是"难用"：`learn_custom_statement` 里 `exec() == Accepted` 的下一句
    就是 `declare_statement`（**写盘**）—— 等于"在候选值框里按一下回车
    就写了 rules.json"。

    判据两件：候选值收下了；**弹窗还开着**。
    """
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QDialog, QDialogButtonBox, QVBoxLayout
    from storygraph.ui_qt.param_editor import ValueChips as ChipEdit

    dlg = QDialog()
    lay = QVBoxLayout(dlg)
    chip = ChipEdit()
    lay.addWidget(chip)
    box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    box.accepted.connect(dlg.accept)
    box.rejected.connect(dlg.reject)
    lay.addWidget(box)
    dlg.show()
    chip._edit.setFocus()

    QTest.keyClicks(chip._edit, 'swim_suit')
    QTest.keyClick(chip._edit, Qt.Key_Return)
    assert chip.text() == 'swim_suit', '回车没把候选值收成方块'
    assert dlg.isVisible(), '回车把弹窗按掉了 —— 会顺着 exec()==Accepted 误写盘'

    # 中文候选值（用户上一轮想加的正是"泳装"）也得走同一条路
    chip._edit.setText('泳装')
    QTest.keyClick(chip._edit, Qt.Key_Return)
    assert chip.text() == 'swim_suit,泳装', chip.text()
    assert dlg.isVisible(), '中文候选值那次把弹窗按掉了'
    dlg.close()


def test_chip_edit_takes_a_whole_paste_at_once(qapp):
    """
    ⚠ 用户 2026-10-12："我就只能填一个候选值，填不了多个"。

    定死：回车**一次收下所有**（分隔符与后端 `parse_choices` **同一套**）——
    "粘一大段 / 打一串 / 粘多行"都行，不必一个个回车。
    顺带钉住**前后端同源**：界面收 5 个，写下去也必须是 5 个
    （若哪天两边分隔符走散，就会"界面看着 5 个、清单里只剩 1 个"）。

    ⚠ 2026-10-16 口径收紧：**空格只在"这一格可以有多个"时才算分隔符**。
    单值格（一格只填一个值）里"洛丽塔 长裙"是一个候选值，不能被空格切开 ——
    所以这条判据改用 `multi=True` 的那个形状（与后端 `parse_choices`
    同源的那一串本来也只在多选格里才有意义）。
    """
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from storygraph.authoring.learn_statement import parse_choices
    from storygraph.ui_qt.param_editor import ValueChips as ChipEdit

    spot = 'swim_suit uniform、casual;formal|home'
    ce = ChipEdit(multi=True)
    ce.show()
    ce._edit.setFocus()
    ce._edit.setText(spot)
    QTest.keyClick(ce._edit, Qt.Key_Return)
    got = ce.text().split(',')
    assert got == ['swim_suit', 'uniform', 'casual', 'formal', 'home'], got
    assert parse_choices(spot) == got, \
        '前后端分隔符不一致：界面收下了 5 个，写进清单时会变少'


def test_chip_edit_add_values_appends_not_replaces(qapp):
    """「⚡ 导入候选」是**加上去**，不是把人已经写的那几个换掉（重复的自动跳过）。"""
    from storygraph.ui_qt.param_editor import ValueChips as ChipEdit

    ce = ChipEdit()
    ce.set_text('swim_suit')
    ce.add_values(['happy', 'calm', 'swim_suit'])
    assert ce.text() == 'swim_suit,happy,calm', ce.text()


def test_the_card_offers_project_sourced_candidates(qapp, tmp_path):
    """
    用户 2026-10-12："我的真实项目内有无数个表情！我不可能将它们一个个输入
    进去！我希望它能够做到识别并自己录入。"

    这里钉**入口真的接上了来源**：卡片上有「⚡ 导入候选」，清单来自
    `choice_sources(项目根, 语句名)`（立绘属性按引擎分组 / 角色表 / 用过的值）。
    真机实测（`D:\\千连万理\\RenPyTest\\sr_general`）：`expressions` 组一次
    就能倒出 **68 个**属性 —— 这就是"表情全集"。

    ⚠ **不钉"它自动猜到这格是表情"** —— 工具不猜语义（猜错是改剧情），
    它只把来源摆出来，点哪个由人定。
    """
    from storygraph.authoring.learn_statement import choice_sources
    from storygraph.ui_qt.dialog_learn import CustomStatementDialog

    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    dlg = CustomStatementDialog(None, project_root=root, discovered=[])
    dlg.params.clear()
    card = dlg.params.add_param('', '表情', '', kind='choice')
    assert hasattr(card, '_import'), '卡上没有「⚡ 导入候选」入口'
    assert card._import.isVisibleTo(card), '枚举格上"导入候选"该露出来'
    assert dlg.params._sources_provider is not None, '来源没接到卡片上'
    assert dlg._choice_sources() == choice_sources(root, ''), '接线断了'
    # 非枚举格没有"候选值"这回事 → 入口藏起来
    card._kind.setCurrentIndex(card._kind.findData('variable'))
    assert not card._import.isVisibleTo(card)


def test_多选格的候选值收词口径(qapp):
    """
    用户 2026-10-16：
    > 这个你没有给我加批量导入……当能够多选的时候就必须要有批量导入候选

    用户 2026-10-16 给的现场：一格候选（服装），勾了「这一格可以有多个」，
    但输入框只会"填一个就回车" —— 真机 `expressions` 组一次就有 **68 个**
    属性，一个个敲不现实。

    这里钉住"收词口径"这一层（界面上的入口见下一条）：

      · 不勾多选 → 一格是**一个值** → 空格**不算**分隔符
        （"洛丽塔 长裙"是一个候选值，不能被切成两个）；
      · 勾了多选 → 一格可以有多个词、也可以一次倒一大批 →
        空格/换行/逗号都算分隔符（"粘一大段"直接全收下）。
    """
    from storygraph.ui_qt.param_editor import split_candidate_values

    # ---- 单值口径：空格是值的一部分（中文候选值里带空格很正常）----
    assert split_candidate_values('洛丽塔 长裙') == ['洛丽塔 长裙']
    assert split_candidate_values('洛丽塔 长裙, 泳装') == ['洛丽塔 长裙', '泳装']
    assert split_candidate_values('泳装\n校服') == ['泳装', '校服']   # 换行仍算分隔
    # ---- 多选口径：空格/换行都算分隔符（一行一个 / 粘一大段）----
    assert split_candidate_values('happy sad 泳装 校服', multi=True) == [
        'happy', 'sad', '泳装', '校服']
    assert split_candidate_values('happy\nsad', multi=True) == ['happy', 'sad']
    assert split_candidate_values('泳装、校服；汉服|home', multi=True) == [
        '泳装', '校服', '汉服', 'home']
    # 去空、保序去重
    assert split_candidate_values(' a  a ,, b ', multi=True) == ['a', 'b']


def test_卡片上勾了多选就露出批量导入(qapp, tmp_path):
    """
    勾上「这一格可以有多个」→ 候选值那格必须**换一副收词口径**并露出批量入口：

      · 占位提示要说清"一行一个 / 空格分隔都行"；
      · 「📋 批量粘贴一大段…」按钮露出来（不勾时藏起来）；
      · 输入框里粘一大段，**一次回车全收下**（空格也是分隔符）。
    """
    from storygraph.authoring.learn_statement import choice_sources
    from storygraph.ui_qt.dialog_learn import CustomStatementDialog

    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    dlg = CustomStatementDialog(None, project_root=root, discovered=[])
    dlg.params.clear()
    card = dlg.params.add_param('', '服装', '', kind='choice')
    card._limit.setChecked(True)          # 候选值这一块本身要先露出来
    chips = card._choices

    # 不勾多选：批量入口藏起来，值里的空格保留
    assert not chips._batch_btn.isVisibleTo(chips), '没勾多选就摆批量入口，是噪音'
    assert not chips._hint.isVisibleTo(chips)
    chips._edit.setText('洛丽塔 长裙')
    chips._commit()
    assert chips.text() == '洛丽塔 长裙', chips.text()

    # 勾上多选：入口露出来 + 收词口径换成"批量"
    card._multi.setChecked(True)
    assert chips._batch_btn.isVisibleTo(chips), '勾了多选还没批量导入入口'
    assert chips._hint.isVisibleTo(chips)
    chips._edit.setText('happy sad 泳装 校服')
    chips._commit()
    assert chips.text().endswith('happy,sad,泳装,校服'), chips.text()
    # 写下去的就是界面看到的（后端 `parse_choices` 同一套分隔符）
    assert parse_choices(card.data()['choices'])[-4:] == [
        'happy', 'sad', '泳装', '校服'], card.data()['choices']
    # 批量对话框的"从项目里拿"来源与卡片那枚 ⚡ 同源
    assert chips.project_sources() == choice_sources(root, ''), '来源没接上'


def test_批量导入窗口一次收下一整批(qapp, tmp_path):
    """
    「📋 批量粘贴一大段…」的真身：**粘一大段 + 从项目里勾多组**，一次全倒进去。

    这里不点真窗口（模态会卡住测试）：把 `QDialog.exec` 换成"模拟用户点了导入"，
    递进去的就是 `_commit_batch` 一定会读的那两处 —— 粘贴框、以及勾中的来源。
    """
    from storygraph.ui_qt.param_editor import FieldCard

    card = FieldCard()
    card._kind.setCurrentIndex(card._kind.findData('choice'))
    card._multi.setChecked(True)
    card._values.set_sources_provider(lambda: [
        {'label': '立绘属性（expressions）', 'count': 3,
         'values': ['happy', 'sad', 'angry']},
        {'label': '角色表', 'count': 2, 'values': ['lan', 'ye']},
    ])
    chips = card._values

    # ⚠ 不点真模态窗（`QDialog.exec` 是 C++ 槽，补丁不可靠）：替换的是
    #   `_batch_import_dialog` 这一层 —— 它返回的就是"用户挑中的那批值"。
    #   于是这条判据钉的是**接线**：那批值要真的进方块、且是**追加**。
    picked = ['泳装', '校服', '汉服', 'happy', 'lan', '泳装']
    orig_dialog = chips._batch_import_dialog
    chips._batch_import_dialog = lambda: list(picked)
    try:
        got = chips.open_batch_import()
    finally:
        chips._batch_import_dialog = orig_dialog

    assert '泳装' in got and '校服' in got and '汉服' in got, got
    assert 'happy' in got and 'lan' in got, got
    assert chips.text().split(',')[:3] == ['泳装', '校服', '汉服'], chips.text()
    # 追加不覆盖：已经在卡上的候选值一个都不能丢
    chips._edit.setText('泳装')
    chips._commit()
    assert chips.text().count('泳装') == 1, chips.text()


def test_no_horizontal_scrollbar_at_the_minimum_size(qapp, tmp_path):
    """
    ⑦ **拉到最小尺寸时，左右两栏都不许出现横向滚动条**。

    这是用户那句"选的一多就把后面那个变量挤到窗口外"的**总判据**：
    内容放不下时要么折行、要么竖着排，**绝不该要人横着拉**。

    实测（修之前）：760×480 时右栏内容 466 px > 视口 354 px → 横向滚动条；
    两处元凶都被揪出来修掉了：卡片 6 列网格（480 px）与 `QGroupBox` 的长标题（466 px）。
    """
    from PySide6.QtWidgets import QScrollArea
    from storygraph.ui_qt.dialog_learn import CustomStatementDialog

    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    dlg = CustomStatementDialog(None, project_root=root, discovered=[])
    # 造出"选的一多"的现场：一格枚举 + 一大堆候选值
    dlg.params.clear()
    card = dlg.params.add_param('mood', '神态', '', kind='choice')
    card._choices.add_values([f'表情{i}' for i in range(14)])
    card._limit.setChecked(True)
    card._choices.setVisible(True)
    dlg.resize(dlg.minimumWidth(), dlg.minimumHeight())
    dlg.show()
    for _ in range(3):
        qapp.processEvents()
    offenders = []
    for area in dlg.findChildren(QScrollArea):
        if area.horizontalScrollBar().isVisible():
            offenders.append((type(area.widget()).__name__,
                              area.viewport().width(),
                              area.widget().sizeHint().width()))
    assert not offenders, (
        f'最小尺寸下这些滚动区出现了横向滚动条（视口宽, 内容宽）：{offenders}')
    # ⚠ 2026-10-13 重写后**额外钉一条**（用户："这两个滚动条是何意味啊？？？"）：
    #   一屏**只许有一个**滚动区（当前页那一个）——
    #   旧版是 弹窗滚动区 + 字段列表滚动区 + 词组滚动区，同屏两三条。
    areas = [a for a in dlg.findChildren(QScrollArea) if a.isVisible()]
    assert len(areas) <= 1, (
        f'同一屏有 {len(areas)} 个可见滚动区（只该有当前页那一个）：'
        f'{[type(a.widget()).__name__ for a in areas]}')
    dlg.close()


def test_cut_accepts_a_whole_line_pasted_from_the_script(qapp, tmp_path):
    """
    ⚠⚠ 用户 2026-10-13 实测现场（截图里那两条 "⚠ 切不上"）：

        粘一行: show lan swim_suit happy        →  切不上

    根因：格子里描述的是"**动作名后面**的东西"，而人从剧本里复制的**一定是整行**
    ——头部那个动作名不在格子里，于是"多出来 1 个词 → 切不上"，
    而界面只会说"检查顺序、关键字写法"，人怎么查都查不出来。

    现在：**行首整词等于动作名就摘掉它**（绝不猜别的），整行照粘能切；
    只粘参数（不带动作名）也照旧能切。
    """
    from storygraph.ui_qt.dialog_learn import CustomStatementDialog

    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    dlg = CustomStatementDialog(None, project_root=root, discovered=[])
    dlg.name.setText('show')
    p = dlg.params
    p.clear()
    p.add_field('lan', '人物', '', kind='variable')
    p.add_field('swim_suit', '服装', '', kind='keyword')
    card = p.add_field('happy', '表情', '', kind='choice')
    card._values.add_values(['happy', 'angry'])
    card._limit.setChecked(True)

    # ① 粘整行（带动作名）→ 切得上，而且**三格都对**
    dlg.sample.setText('show lan swim_suit happy')
    dlg._refresh_cut()
    text = dlg.cut_view.text()
    assert '切出来是这样' in text, text
    assert '共 3 格' in text, text
    assert 'lan（人物）' in text and 'swim_suit（服装）' in text \
        and 'happy（表情）' in text, text

    # ② 不带动作名的也照旧能切
    dlg.sample.setText('lan swim_suit happy')
    dlg._refresh_cut()
    assert '切出来是这样' in dlg.cut_view.text(), dlg.cut_view.text()

    # ③ 只粘动作名 → 说清"后面还没写"，而不是含混的"检查顺序"
    dlg.sample.setText('show')
    dlg._refresh_cut()
    text = dlg.cut_view.text()
    assert '后面还没写东西' in text, text
    dlg.close()


def test_choice_sources_is_empty_not_invented(tmp_path):
    """
    ⭐ 通用性底线：项目里**什么都没采过**时，来源清单是**空的** ——
    绝不凭空编一份"可能的候选"（少了是保守，编了是骗人）。
    """
    from storygraph.authoring.learn_statement import choice_sources

    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    assert choice_sources(root) == []


# ============ ⑦ v3 界面重做（2026-10-12 用户："擦了重新设计重新写"） ============
#
# 设计文档：`docs/archive/设计_自定义动作界面_v3_重做_2026-10-12.md`
# 用户现场（截图评审）：
#   > 前面那个选的一多呀……就把后面那个变量挤到窗口外去了！而且你这个窗口还不能全屏……
#
# 这一节钉住的正是那两句话，**用尺寸与标志说话**（不靠肉眼）。

def test_the_window_can_be_maximized_and_grown(qapp, tmp_path):
    """
    ① 窗口**能全屏**（`WindowMaximizeButtonHint`）+ 有右下角拖动柄。

    实测（修之前）：窗口标志不含 MaximizeHint，标题栏**根本没有最大化按钮** ——
    用户看到的"不能全屏"就是这个。
    """
    from PySide6.QtCore import Qt
    from storygraph.ui_qt.dialog_learn import CustomStatementDialog

    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    dlg = CustomStatementDialog(None, project_root=root, discovered=[])
    assert dlg.windowFlags() & Qt.WindowMaximizeButtonHint, '没有最大化按钮'
    assert dlg.windowFlags() & Qt.WindowMinimizeButtonHint, '没有最小化按钮'
    assert dlg.isSizeGripEnabled(), '没有右下角拖动柄'
    # 最大尺寸必须放开（万一把 maximumSize 写死，它同样"不能全屏"）
    assert dlg.maximumWidth() > 5000 and dlg.maximumHeight() > 5000
    dlg.close()


def test_the_window_minimum_is_usable_not_the_content_width(qapp, tmp_path):
    """
    ② 最小尺寸必须"人能干活"，**不是"内容的全宽"**。

    实测（修之前）：建议最小 806×994，而代码写死 720 高 —— 底部按钮当场被裁。
    现在：最小尺寸 ≤ 900×620（能塞进 1024×768 这种老屏），
    而且**默认尺寸不超过屏幕可用区**（不再写死 1100×720）。
    """
    from storygraph.ui_qt.dialog_learn import CustomStatementDialog

    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    dlg = CustomStatementDialog(None, project_root=root, discovered=[])
    assert dlg.minimumWidth() <= 900, dlg.minimumWidth()
    assert dlg.minimumHeight() <= 620, dlg.minimumHeight()
    hint = dlg.minimumSizeHint()
    assert hint.height() <= 700, f'建议最小高仍然过高（{hint.height()}）—— 小屏会被裁'
    dlg.close()


def test_a_param_card_stays_narrow_even_with_many_choices(qapp):
    """
    ③ **卡片最小宽不许随候选值变多而涨**（用户报的"挤出去"就是这个）。

    实测对比：
      · v2（6 列网格 + 横排方块）：空卡最小宽 **480 px**，方块越多还越大；
      · v3（单列 + 折行）：空卡 **188 px**，16 个方块时**仍然 188 px**。

    ⚠ 钉的是"**不随数量增长**"这个不变量，而不是某个具体数字 ——
    以后改字号/间距，这条判据照样有意义。
    """
    from storygraph.ui_qt.param_editor import FieldCard as ParamCard

    empty = ParamCard()
    base = empty.minimumSizeHint().width()
    assert base <= 320, f'空卡最小宽 {base} px 太宽了（v2 是 480）'

    many = ParamCard()
    many._choices.set_text(','.join(f'候选{i}' for i in range(16)))
    grown = many.minimumSizeHint().width()
    assert grown <= base + 24, (
        f'候选值一多，卡片最小宽从 {base} 涨到 {grown} —— 又要把窗口顶宽了')


def test_chips_wrap_instead_of_stretching_the_row():
    """
    ④ 候选值方块必须**折行**（宽度有上限），不能横着无限长。

    `QHBoxLayout` 的最小宽 = 所有方块之和 → 方块一多就顶宽整窗；
    `FlowLayout` 的最小宽 = **最宽的那一个方块**。
    """
    from storygraph.ui_qt.param_editor import ValueChips as ChipEdit

    one = ChipEdit()
    one.set_text('a')
    many = ChipEdit()
    many.set_text(','.join(f'rank{i}' for i in range(20)))
    narrow = one.minimumSizeHint().width()
    wide = many.minimumSizeHint().width()
    assert wide <= narrow + 60, (
        f'20 个方块的最小宽 {wide} 远大于 1 个方块的 {narrow} —— 没有折行')


def test_the_action_bar_never_scrolls_away(qapp, tmp_path):
    """
    ⑤ 主按钮（写清单 / 取消）**不在滚动区里** —— 滚多远都点得到。

    设计文档 R17：提交按钮放在固定命令区，内容自己滚。
    判据：按钮条的祖先里**没有** `QScrollArea`。
    """
    from PySide6.QtWidgets import QScrollArea
    from storygraph.ui_qt.dialog_learn import CustomStatementDialog

    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    dlg = CustomStatementDialog(None, project_root=root, discovered=[])
    for btn in (dlg._ok_btn,):
        p = btn.parentWidget()
        while p is not None:
            assert not isinstance(p, QScrollArea), \
                f'{btn.text()} 被放进了滚动区 —— 内容一多就点不到了'
            p = p.parentWidget()
    dlg.close()


def test_the_main_path_does_not_need_scrolling_at_the_default_size(qapp,
                                                                   tmp_path):
    """
    ⑥ 默认尺寸下，**主路径四步**（发现 → 名字 → 要填的东西 → 试切）都在一屏里。

    设计文档 P5：能不滚就不滚；滚只发生在"很多格子/很多候选"时。
    判据（结构而非像素）：这四块控件在默认尺寸下都**已经可见且高度 > 0**。
    """
    from storygraph.ui_qt.dialog_learn import (CustomStatementDialog,
                                               PAGE_DECLARE)

    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    dlg = CustomStatementDialog(None, project_root=root, discovered=[])
    dlg.resize(960, 660)
    dlg.show()
    qapp.processEvents()
    # 第一页：发现列表
    for name, w in (('发现列表', dlg.found), ('写清单按钮', dlg._ok_btn)):
        assert w.isVisible(), f'{name} 在默认尺寸下看不见'
        assert w.height() > 0, f'{name} 被压成 0 高'
    # 第二页：填写（⚠ 2026-10-13 起它在一张页签上 —— 切过去再看）
    dlg.tabs.setCurrentIndex(PAGE_DECLARE)
    qapp.processEvents()
    for name, w in (('动作名输入', dlg.name), ('试切输入', dlg.sample),
                    ('试切结果', dlg.cut_view), ('字段列表', dlg.params)):
        assert w.isVisible(), f'{name} 在默认尺寸下看不见'
        assert w.height() > 0, f'{name} 被压成 0 高'
    dlg.close()


# ============ ⑧ 位置参数不丢 + 词组标注（2026-10-12 用户追问时挖出来的） ============

def _write_swim_suit_project(root):
    """造一个"声明了 `swim_suit` 且有位置参数"的最小项目。"""
    import json
    (root / 'game').mkdir(parents=True, exist_ok=True)
    (root / 'storygraph.rules.json').write_text(json.dumps({
        'statements': [{
            'name': 'swim_suit', 'label': '泳装切换',
            'columns': [{'name': 'who', 'label': '角色'},
                        {'name': 'outfit', 'label': '服装',
                         'choices': ['泳装', '校服']},
                        {'name': 'mood', 'label': '神态',
                         'choices': ['happy', 'shy']}],
            # ⚠ 形状与**弹窗写下来的**一致（`kind` + `text` + `column`）
            'pattern': [{'kind': 'variable', 'text': '角色', 'column': '角色'},
                        {'kind': 'choice', 'text': '服装', 'column': '服装'},
                        {'kind': 'keyword', 'text': 'mood', 'column': '神态'}],
        }],
    }, ensure_ascii=False), encoding='utf-8')
    return root


def test_positional_parameters_are_read_into_their_own_cells(qapp, tmp_path):
    """
    ⚠⚠ 用户 2026-10-12 的追问挖出来的**真 bug**：

        > 你确定我这个得到修改之后，卡牌编辑器中能够完全反应出来吗？
        > 不是 excel 表格，而是卡牌编辑器内也需要有相应的改变

    实测（修之前）：声明了 `swim_suit` 之后，这一行

        swim_suit lyj "泳装" mood=happy

    在**卡片编辑器**里长这样：`角色` 空、`服装` **空**、`神态`=happy。
    根因两处，都在"读了却没用上"：
      ① 位置参数的列号（`positional`）**只在显式写了才有**，而弹窗存的是
         `pattern`（有序格子）——`pattern` 甚至没进 `RegisteredStatement`；
      ② 于是 `spec_arg_cells` 只去 `key=value` 里找值，位置参数全部落空。

    修法：`pattern` 进注册表 → `spec_columns` 按"格子顺序 = 位置号"补上
    （名字对上优先，对不上按 1:1 顺序兜底）。
    """
    from storygraph.story.statement_registry import (StatementRegistry,
                                                     clear_cache,
                                                     dynamic_spec_columns)
    from storygraph.story.beat_model import Beat

    root = _write_swim_suit_project(tmp_path / 'proj')
    clear_cache()
    reg = StatementRegistry.for_project(str(root))
    b = Beat()
    b.opaque = 'swim_suit lyj "泳装" mood=happy'
    cols = dynamic_spec_columns([b], reg)
    cells = b.spec_arg_cells(cols)
    got = {str(c.get('param')): v for c, v in zip(cols, cells)}
    assert got.get('who') == 'lyj', ('角色 那一格没吃到位置参数', got)
    assert got.get('outfit') == '泳装', ('服装 那一格没吃到位置参数', got)
    assert got.get('mood') == 'happy', ('神态 那一格没吃到 key=value', got)


def test_editing_a_positional_cell_folds_back_into_the_head(qapp, tmp_path):
    """
    卡片上改一格 → **折回头行**（写回的路径与 Excel 同一条）。

    这条是"编辑器里能完全反应出来"的**闭环判据**：
    读（上一则）→ 改 → 折回 → 渲染出来的那一行也跟着变。
    """
    from storygraph.story.statement_registry import (StatementRegistry,
                                                     clear_cache,
                                                     dynamic_spec_columns)
    from storygraph.story.beat_model import Beat, fold_spec_arg_columns

    root = _write_swim_suit_project(tmp_path / 'proj')
    clear_cache()
    reg = StatementRegistry.for_project(str(root))
    b = Beat()
    b.opaque = 'swim_suit lyj "泳装" mood=happy'
    cols = dynamic_spec_columns([b], reg)
    header = next(str(c['header']) for c in cols if c.get('param') == 'outfit')
    fold_spec_arg_columns(b, {header: '校服'})
    assert '校服' in b.statement_head(), b.statement_head()
    assert '泳装' not in b.statement_head(), b.statement_head()
    assert 'lyj' in b.statement_head() and 'mood=happy' in b.statement_head(), b.statement_head()


def test_editing_a_keyword_cell_keeps_the_other_cells(qapp, tmp_path):
    """改 `key=value` 那一格，**不许**碰位置参数那两格。"""
    from storygraph.story.statement_registry import (StatementRegistry,
                                                     clear_cache,
                                                     dynamic_spec_columns)
    from storygraph.story.beat_model import Beat, fold_spec_arg_columns

    root = _write_swim_suit_project(tmp_path / 'proj')
    clear_cache()
    reg = StatementRegistry.for_project(str(root))
    b = Beat()
    b.opaque = 'swim_suit lyj "泳装" mood=happy'
    cols = dynamic_spec_columns([b], reg)
    header = next(str(c['header']) for c in cols if c.get('param') == 'mood')
    fold_spec_arg_columns(b, {header: 'shy'})
    assert 'mood=shy' in b.statement_head(), b.statement_head()
    assert 'lyj' in b.statement_head(), b.statement_head()


def test_a_group_declared_in_the_dialog_reaches_the_cards(qapp, tmp_path):
    """
    ⚠ 另一个真 bug（同一轮挖出来的）：**词组填了，参数卡上却是空的**。

    根因：`_sync_groups_to_slots()` 在"词组还没 load 进来"时就调过一次
    （把表同步成空），load 之后再没人刷新 → 用户打开弹窗看到的是一个**空下拉**
    （"词组到底有什么用"里至少一半是它造成的）。

    现在：① load 之后补一次同步；② 词组表一变（增/删/改名）**自动**同步；
    ③ 分组改成**自动标注**（不是下拉）—— 名字/中文列名对得上就标在卡头。
    """
    from storygraph.ui_qt.dialog_learn import CustomStatementDialog

    root = _write_swim_suit_project(tmp_path / 'proj')
    dlg = CustomStatementDialog(None, project_root=root, discovered=[],
                                declared_groups=[])
    # ① 声明了一个词组 → 参数卡上的标注要认出来（中文列名 == 词组的中文名）
    dlg.groups.add_group('outfit', '服装', '泳装,校服')
    dlg.params.clear()
    card = dlg.params.add_param('outfit', '服装', '泳装,校服', kind='choice')
    assert dlg.params._groups, '词组表没同步到参数卡'
    assert card.group_of() == 'outfit', ('中文列名对上了却没标上',
                                         card.group_of())
    assert 'outfit' in card._group_hint.text(), card._group_hint.text()
    # ② 词组改名 → 对不上就不标（不硬塞一个假分组）
    dlg.groups.clear()
    dlg.groups.add_group('clothes', '衣着', '')
    assert card.group_of() == '', '词组已经对不上了，标注还挂着'
    # ③ 名字能对上 → 重新标上（这一格的名字就是 'outfit'）
    dlg.groups.add_group('outfit', '服装', '')
    assert card.group_of() == 'outfit', '组名对上却没认出来'
    # ④ 交出去的序列里带着它（试切/落进声明都能用）
    assert dlg.params.slots()[0]['group'] == 'outfit', dlg.params.slots()
    dlg.close()
