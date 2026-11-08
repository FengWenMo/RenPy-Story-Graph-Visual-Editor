# -*- coding: utf-8 -*-
"""
地狱测试：**扩展 × 卡片编辑器显示**（2026-11-08 第四轮）。

用户原话：

> 很好，现在还有个它和卡片编辑器显示的打了吗？混合起来的也要再打一遍

所以这一组只打一件事：**扩展 / 自定义语句 / 映射这三样，在卡片编辑器上
到底显示成什么样**；而且都不自己拼 JSON —— 扩展点在「扩展」窗口里点出来
（`_new_point` → 种类 → 字段表 → `_save`），自定义语句在「自定义语句」窗口里
教出来，映射在「资源映射」窗口里填出来，然后断言落在**卡片上看得见的东西**
（行标签、控件类、语句条、参数格、下拉里那句人话）与**导出产物**上。

三组攻击面（外加一组三者同时在场）：

  A. 拍字段扩展点 → 卡片上那一格：标签 / 控件 / 提示 / 工具条，以及
     "卡片上那格改了，写回去的还是不是这一拍的值"；
  B. 语句拍 → 卡片上的空块：`泳装切换 …` 这类语句拍不该铺着台词/舞台空块，
     而控制流卡 ⇒ 普通卡来回翻时那两块要按**这一拍**放回来；
  C. 映射 → 卡片上的那格人话：屏幕写 `18 战斗`、写回的还是 `bgm/a.mp3`，
     而且**显示名一个字都不许进脚本**。

这一轮真抓到的缺陷（都已修，注释里都写了"为什么"）：

  ① 扩展窗口明确把 ``Comment`` / ``condition`` 列成可改的键，`card_fields`
     也照单全收 —— 但卡片上那两格是 `_build_meta_area` **建一次**的，
     之后 `set_card_field_specs` 只重建高级区：改完保存，**一个像素都没变**，
     也没有任何提示（"卡片上的格和字段表对不上"的最静默版本）；
  ② 把一格改成「固定关键字」时，那一格变成只读框、里面是那个字 —— 于是
     `collect_fields()` 把那几个字当成**这一拍的内容**交出去
     （`Stage='show'`）。值格里放不下的类型，与开关/多选同一条口径：拒；
  ③ 语句拍的收起组被 `set_flow(None)` 当场放回来：面板单独调是对的
     （`test_card_field_groups.py` 全绿），**真窗口里却是另一回事** ——
     `_sync_spec_args` 先按语句收起、紧接着 `_sync_flow_card` 又全放出来，
     于是语句拍照样铺着四个空块（正是 2026-11-07 要修的那张卡）。

跑法（offscreen 真 Qt，与 `test_ui_qt.py` 同一套）：

    python -m pytest tests/test_hell_extension_card_display.py -q
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')

pytest.importorskip('PySide6')
from PySide6.QtWidgets import QLabel                          # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.story.beat_render import render_beats_to_rpy  # noqa: E402
from storygraph.story.statement_registry import clear_cache    # noqa: E402


# ---------------------------------------------------------------------------
#  工程 / 窗口 / 工具
# ---------------------------------------------------------------------------

#: 普通两句：第一拍带着 `show`，第二拍什么都没有（值格被改成固定字的那条
#: 判据要用"没有值"的拍 —— 有值的话固定字会被载入的值盖住，看不出问题）。
_PLAIN = '''label ch:
    show lan happy
    "一"
    "二"
    return
'''

#: 两条 `mycue`：第一条后面还接着说一句（语句拍 + 台词），第二条在 `return`
#: 前面（语句拍，没有任何台词）。
_MYCUE = {
    'name': 'mycue', 'label': '提示',
    'pattern': [
        {'kind': 'keyword', 'text': 'mycue', 'column': '动作名'},
        {'kind': 'number', 'column': '秒数'},
        {'kind': 'variable', 'column': '内容'},
    ],
}

_STMT_SRC = '''label ch:
    "一"
    mycue 3 "x"
    "二"
    mycue 4 "y"
    return
'''

#: 扩展里声明写法用：一拍 `mycue`，后面跟一句台词。
_DECLARED_SRC = '''label ch:
    mycue 3 "x"
    "一"
    return
'''

#: 控制流：`call` 进另一章，再回来接着说。
_FLOW_SRC = '''label ch:
    "一"
    call side
    "调用之后"
    return

label side:
    "那边"
    return
'''

#: 映射：一条 `play music`，文件名能在映射表里查到中文叫法。
_MUSIC_SRC = '''label ch:
    play music "bgm/a.mp3"
    "一"
    mycue 3 "x"
    return
'''


class _Engine:
    """主窗口要的最小引擎（与 `test_hell_custom_card_layout.py` 同一套）。"""

    project_root = None

    def __init__(self, root):
        self.project_root = str(root)

    def get_registry(self):
        return None


@pytest.fixture(autouse=True)
def _clean_registry_cache():
    """注册表带全局缓存：每个用例前后都清，免得读到上一条装配的那份。"""
    clear_cache()
    yield
    clear_cache()


@pytest.fixture(scope='session')
def app():
    from PySide6.QtWidgets import QApplication
    a = QApplication.instance() or QApplication([])
    yield a


def _project(tmp_path, src, rules=None, name='proj'):
    root = tmp_path / name
    (root / 'game').mkdir(parents=True)
    (root / 'game' / 'ch.rpy').write_text(src, encoding='utf-8')
    if rules is not None:
        (root / 'storygraph.rules.json').write_text(
            json.dumps(rules, ensure_ascii=False, indent=2), encoding='utf-8')
    return root


@pytest.fixture
def make_window(app):
    """真 `MainWindowQt`；用例结束时统一关掉（它们会挂后台缩略图任务）。"""
    from storygraph.ui_qt.main_window import MainWindowQt
    made = []

    def build(root):
        win = MainWindowQt(_Engine(root), root / 'game' / 'ch.rpy')
        made.append(win)
        return win

    yield build
    for win in made:
        try:
            win._panel.shutdown_stage_thumb()
        except Exception:                       # noqa: BLE001 - 关窗失败不拦收尾
            pass
        win.close()


def _show(win, idx):
    """切到第 idx 张卡片（走窗口那条真路：载入字段 → 同步语句参数组）。"""
    win._set_page(win.PAGE_CARD)
    win._show_index(idx)


def _export(win) -> str:
    return render_beats_to_rpy(win.beats, label_name='ch')


def _labels(widget) -> list:
    """控件树里**真的显示出来**的文字（`isVisibleTo` 才认）。"""
    return [w.text() for w in widget.findChildren(QLabel)
            if w.isVisibleTo(widget) and w.text()]


def _idx_of(win, needle: str) -> int:
    """头行里含 `needle` 的那一拍（真拍，不猜）。"""
    for i, beat in enumerate(win.beats):
        head = (getattr(beat, 'spec_head', '') or getattr(beat, 'camera_header', '')
                or getattr(beat, 'loop_header', '') or '')
        if needle in head:
            return i
    raise AssertionError(f'没有头行含 {needle!r} 的拍；'
                         f'现有：[{[(b.seq, getattr(b, "spec_head", "")) for b in win.beats]}]')


def _idx_of_text(win, text: str) -> int:
    for i, beat in enumerate(win.beats):
        if str(getattr(beat, 'text', '') or '') == text:
            return i
    raise AssertionError(f'没有台词 {text!r} 的拍')


def _cell(panel, needle: str):
    """参数格里名字含 `needle` 的那一格（键名带 `@位置`，别写死）。"""
    for key, widget in (panel._spec_widgets or {}).items():
        if needle in key:
            return key, widget
    raise AssertionError(f'没有含 {needle!r} 的参数格：'
                         f'{list(panel._spec_widgets or {})}')


# ---------------------------------------------------------------- 扩展窗口

def _drive_extensions(win, driver, monkeypatch):
    """
    走**真入口** `open_extensions`：里面的 `dlg.exec()` 换成"跑一遍 driver"。

    为什么非这么绕：`_refresh_card_field_specs()` 是 `open_extensions` 在
    对话框关掉之后调的 —— 要是这里直接 `ExtensionDialog(...)` 再手工调刷新，
    就绕过了"保存完卡片会不会跟着变"这条接线（本轮 ① 正是死在这条线上）。
    """
    from storygraph.ui_qt import dialog_extensions as DE
    seen = {}

    def fake_exec(self):
        seen['dlg'] = self
        driver(self)
        return 1

    monkeypatch.setattr(DE.ExtensionDialog, 'exec', fake_exec)
    win.open_extensions()
    return seen['dlg']


def _field_point(dlg, ident, key, *, kind='variable', label='', literal='',
                 choices=''):
    """＝ 点「＋ 新建」→ 选"拍字段" → 加一格 → 填表 → 保存（全是界面动作）。"""
    dlg._new_point()
    dlg.ed_id.setText(ident)
    dlg.ed_label.setText(ident)
    dlg.cb_kind.setCurrentIndex(dlg.cb_kind.findData('field'))
    dlg._add_field()
    row = dlg.tbl.rowCount() - 1
    dlg.tbl.item(row, 0).setText(key)          # 键名
    combo = dlg.tbl.cellWidget(row, 2)         # 类型
    combo.setCurrentIndex(combo.findData(kind))
    dlg.tbl.item(row, 3).setText(literal)      # 固定字
    dlg.tbl.item(row, 4).setText(label or key)  # 标签
    dlg.tbl.item(row, 5).setText(choices)      # 候选
    assert dlg._save(), dlg.status.text()


def _declare_statement(dlg, ident, columns, label=''):
    """在扩展窗口里声明一条**写法**（kind=statement）：键名 = 列名。"""
    dlg._new_point()
    dlg.ed_id.setText(ident)
    dlg.ed_label.setText(label or ident)
    dlg.cb_kind.setCurrentIndex(dlg.cb_kind.findData('statement'))
    for key, kind in columns:
        dlg._add_field()
        row = dlg.tbl.rowCount() - 1
        dlg.tbl.item(row, 0).setText(key)
        combo = dlg.tbl.cellWidget(row, 2)
        combo.setCurrentIndex(combo.findData(kind))
        dlg.tbl.item(row, 4).setText(key)
    assert dlg._save(), dlg.status.text()


# ------------------------------------------------------- 自定义语句 / 映射

def _teach(root, name, sample):
    """在「自定义语句」窗口里教一条写法（真控件）。"""
    from storygraph.ui_qt.dialog_learn import CustomStatementDialog
    dlg = CustomStatementDialog(None, project_root=str(root))
    try:
        dlg._new_statement()
        dlg.name.setText(name)
        dlg.sample.setText(sample)
        dlg._autofill_from_sample()
        assert dlg._save_statement(), dlg._status.text()
    finally:
        dlg.close()


def _map_music(root, ident='bgm/a.mp3', label='战斗', number='18'):
    """在「资源映射」窗口里加一条中文叫法（真控件），保存到项目根。"""
    from storygraph.ui_qt.mapping_dialog import MappingDialog
    dlg = MappingDialog(None, project_root=str(root))
    try:
        dlg._editor.load({'schema': 'storygraph.mapping/1'})
        dlg._editor.set_section('vocabulary')
        row = dlg._editor.add_row()
        assert row >= 0
        for column, value in ((1, 'music'), (2, ident), (3, label), (4, number)):
            assert dlg._editor.set_cell(row, column, value) == ''
        assert dlg.prepare_save(), dlg._status.text()
        assert dlg.confirm_save(), dlg._status.text()
    finally:
        dlg.close()
    return root / 'storygraph.mapping.json'


# ---------------------------------------------------------------------------
#  A. 拍字段扩展点 → 卡片上那一格
# ---------------------------------------------------------------------------

class TestFieldPointReachesTheCard:

    def test_a_main_area_cell_follows_the_field_table(self, make_window,
                                                      tmp_path, monkeypatch):
        """
        `Comment` 在扩展窗口的说明里是**第一批**列出来的可改键。

        它是主区那两格之一，不是高级区 —— 所以"重建高级区"这条链路到不了它。
        改完保存，卡片上那行必须真的换标签/换控件，否则字段表说的和卡片
        显示的就不是一回事（还一声不吭）。
        """
        root = _project(tmp_path, _PLAIN)
        win = make_window(root)
        _show(win, 0)
        panel = win._panel
        panel._set_value('Comment', '旁注：这张卡要重画')

        def driver(dlg):
            _field_point(dlg, '备注框', 'Comment', kind='code', label='制作备注')

        _drive_extensions(win, driver, monkeypatch)
        assert type(panel._widgets['Comment']).__name__ == 'CodeEditor', \
            '主区那格没跟着字段表重建（改的是 Comment，重建的只有高级区）'
        assert '制作备注' in _labels(panel), _labels(panel)
        # 重建控件最容易丢的就是值（高级区那条判据钉过，主区这条以前根本没有）
        assert panel._get_value('Comment') == '旁注：这张卡要重画'

    def test_the_condition_cell_follows_the_field_table(self, make_window,
                                                       tmp_path, monkeypatch):
        """`condition` 与 `Comment` 同一条链：它也在主区，也必须跟着变。"""
        root = _project(tmp_path, _PLAIN)
        win = make_window(root)
        _show(win, 0)
        panel = win._panel

        def driver(dlg):
            _field_point(dlg, '条件格', 'condition', kind='multiline',
                         label='什么时候才出现')

        _drive_extensions(win, driver, monkeypatch)
        assert type(panel._widgets['condition']).__name__ == 'QPlainTextEdit', \
            '条件那格没跟着字段表重建'
        assert '什么时候才出现' in _labels(panel), _labels(panel)

    def test_the_stage_cell_becomes_a_code_box_and_keeps_its_text(
            self, make_window, tmp_path, monkeypatch):
        """高级区那格（对照组）：换成代码框、标签换了、值一个字没丢。"""
        root = _project(tmp_path, _PLAIN)
        win = make_window(root)
        _show(win, 0)
        panel = win._panel
        assert panel._get_value('Stage') == 'show lan happy'

        def driver(dlg):
            _field_point(dlg, '舞台动作', 'Stage', kind='code', label='舞台动作')

        _drive_extensions(win, driver, monkeypatch)
        assert type(panel._widgets['Stage']).__name__ == 'CodeEditor'
        assert '舞台动作' in _labels(panel._adv_host), _labels(panel._adv_host)
        assert panel._get_value('Stage') == 'show lan happy'

    def test_the_advanced_tooltip_names_the_new_label(self, make_window,
                                                     tmp_path, monkeypatch):
        """
        「高级区」那个按钮的 tooltip 写着里面有哪些格 —— 它也是**字段表画的**。

        行标签换了、tooltip 还念旧名字，等于卡片自己说"里面是 Stage"。
        """
        root = _project(tmp_path, _PLAIN)
        win = make_window(root)
        _show(win, 0)
        panel = win._panel

        def driver(dlg):
            _field_point(dlg, '舞台动作', 'Stage', kind='code', label='舞台动作')

        _drive_extensions(win, driver, monkeypatch)
        assert '舞台动作' in panel._adv_btn.toolTip(), panel._adv_btn.toolTip()

    def test_a_fixed_word_is_refused_and_never_becomes_content(
            self, make_window, tmp_path, monkeypatch):
        """
        值格里放不下"固定字"：那个字不是这一拍的内容。

        以前它只读地显示在那儿，`collect_fields()` 却把它当值交出去 ——
        一张**没有舞台内容**的拍就这样凭空多了一句 `show`。
        """
        root = _project(tmp_path, _PLAIN)
        win = make_window(root)
        logged = []
        win._log = logged.append
        _show(win, 1)                       # "二"：没有舞台内容
        panel = win._panel
        assert panel._get_value('Stage') == ''

        def driver(dlg):
            _field_point(dlg, '死字', 'Stage', kind='keyword', literal='show')

        _drive_extensions(win, driver, monkeypatch)
        # 卡片照旧画原来那一格，且它**不许**变成内容
        assert type(panel._widgets['Stage']).__name__ == 'QPlainTextEdit', \
            '固定字被画成了只读框（值格没有"写死的字"这回事）'
        assert panel._get_value('Stage') == '', panel._get_value('Stage')
        assert not panel.collect_fields().get('Stage')
        said = ' '.join(str(x) for x in logged)
        assert '固定字' in said, said

    def test_a_switch_is_refused_from_the_dialog(self, make_window, tmp_path,
                                                 monkeypatch):
        """开关同一条口径（已有判据是纯函数那层，这里补"从窗口点出来"）。"""
        root = _project(tmp_path, _PLAIN)
        win = make_window(root)
        logged = []
        win._log = logged.append
        _show(win, 0)
        panel = win._panel

        def driver(dlg):
            _field_point(dlg, '开关', 'Stage', kind='bool')

        _drive_extensions(win, driver, monkeypatch)
        assert type(panel._widgets['Stage']).__name__ == 'QPlainTextEdit'
        assert '开关' in ' '.join(str(x) for x in logged)

    def test_saving_the_point_does_not_touch_the_script(self, make_window,
                                                        tmp_path, monkeypatch):
        """扩展点存进 `storygraph.ext.json` —— 剧本一个字节都不许动。"""
        root = _project(tmp_path, _PLAIN)
        win = make_window(root)
        _show(win, 0)
        before = (root / 'game' / 'ch.rpy').read_bytes()

        def driver(dlg):
            _field_point(dlg, '舞台动作', 'Stage', kind='code', label='舞台动作')

        _drive_extensions(win, driver, monkeypatch)
        assert (root / 'game' / 'ch.rpy').read_bytes() == before
        assert (root / 'storygraph.ext.json').exists()

    def test_switching_pages_keeps_the_new_rows(self, make_window, tmp_path,
                                                monkeypatch):
        """翻到表格页再翻回来：字段表那几格不许退回旧样子。"""
        root = _project(tmp_path, _PLAIN)
        win = make_window(root)
        _show(win, 0)
        panel = win._panel

        def driver(dlg):
            _field_point(dlg, '舞台动作', 'Stage', kind='code', label='舞台动作')

        _drive_extensions(win, driver, monkeypatch)
        win._set_page(win.PAGE_GRAPH)
        _show(win, 0)
        assert type(panel._widgets['Stage']).__name__ == 'CodeEditor'
        assert '舞台动作' in _labels(panel._adv_host)

    def test_deleting_the_point_puts_every_row_back(self, make_window, tmp_path,
                                                    monkeypatch):
        """删掉那条扩展点 → 主区与高级区**都**回到内置的样子。"""
        from storygraph.ui_qt import dialog_extensions as DE
        root = _project(tmp_path, _PLAIN)
        win = make_window(root)
        _show(win, 0)
        panel = win._panel

        def add(dlg):
            _field_point(dlg, '备注框', 'Comment', kind='code', label='制作备注')

        _drive_extensions(win, add, monkeypatch)
        assert type(panel._widgets['Comment']).__name__ == 'CodeEditor'

        class _YesBox:
            Yes = 1

            @staticmethod
            def question(*_a, **_k):
                return 1

            @staticmethod
            def information(*_a, **_k):
                return None

        monkeypatch.setattr(DE, 'QMessageBox', _YesBox)

        def delete(dlg):
            assert dlg.focus_on('备注框')
            dlg.btn_del.click()
            assert dlg._reg.get('备注框') is None, dlg.status.text()

        _drive_extensions(win, delete, monkeypatch)
        assert type(panel._widgets['Comment']).__name__ == 'QLineEdit'
        assert '备注' in _labels(panel), _labels(panel)

    def test_the_dialog_preview_and_the_card_agree(self, make_window, tmp_path,
                                                   monkeypatch):
        """
        扩展窗口里"预览"那一格和卡片上那一格必须是**同一画法**。

        两边都走 `ui_qt.field_widgets.make_editor`；这条判据比的是控件类
        （用户的话：卡片右边的 UI 和自定义模块定义出的模块一定要对应）。
        """
        root = _project(tmp_path, _PLAIN)
        win = make_window(root)
        _show(win, 0)
        panel = win._panel
        seen = {}

        def driver(dlg):
            _field_point(dlg, '备注框', 'Comment', kind='code', label='制作备注')
            # 保存前：窗口里那张"预览"已经画成代码框了
            seen['preview'] = type(dlg.form._editors['Comment'][1]).__name__

        _drive_extensions(win, driver, monkeypatch)
        assert seen['preview'] == 'CodeEditor', seen
        assert type(panel._widgets['Comment']).__name__ == seen['preview'], \
            '窗口预览与卡片不是同一种控件'


# ---------------------------------------------------------------------------
#  B. 语句拍 × 卡片上的空块
# ---------------------------------------------------------------------------

@pytest.fixture
def taught_window(make_window, tmp_path):
    """工程自带一条 `mycue` 声明（项目里本来就有 `rules.json` 的那种情况）。"""
    root = _project(tmp_path, _STMT_SRC, rules={'statements': [_MYCUE]})
    win = make_window(root)
    return win


class TestStatementCardBlocks:

    def test_a_statement_beat_does_not_show_the_empty_blocks(self, taught_window):
        """
        最后那条 `mycue 4 "y"` 没有台词、没有舞台动作 —— 卡片上不该铺着
        「说话人 / 台词」和「背景 / 立绘 / 音乐」两块空框。

        2026-11-07 用户原话：「一条 `泳装切换 …` 的卡片上，仍然铺着四个空块
        —— 那些字段跟这条语句毫无关系，却占掉右栏大半。」
        """
        win = taught_window
        idx = _idx_of(win, 'mycue 4')
        _show(win, idx)
        panel = win._panel
        assert panel._stmt_bar.isVisibleTo(panel), '语句条没显示'
        assert panel._dialogue_box.isHidden(), '语句拍还铺着台词框'
        assert panel._stage_box.isHidden(), '语句拍还铺着背景/立绘/音乐那几格'

    def test_a_statement_beat_with_a_line_keeps_only_the_dialogue(
            self, taught_window):
        """语句拍顺带一句台词：台词要看得见，舞台块仍是噪音。"""
        win = taught_window
        idx = _idx_of(win, 'mycue 3')
        _show(win, idx)
        panel = win._panel
        assert not panel._dialogue_box.isHidden(), \
            '这一拍有台词（"二"），台词框被收起来了'
        assert panel._stage_box.isHidden(), '语句拍没动舞台，那一块还铺着'

    def test_a_play_music_beat_without_a_line_hides_the_dialogue(
            self, make_window, tmp_path):
        """没有台词就不是台词拍 —— 台词框对谁都是噪音（不分类别）。"""
        root = _project(tmp_path, '''label ch:
    "一"
    play music "bgm/a.mp3"
    return
''')
        win = make_window(root)
        idx = _idx_of_text(win, '一')
        _show(win, idx)
        panel = win._panel
        assert not panel._dialogue_box.isHidden()
        win._show_index(idx + 1)                # 音乐那一拍（没有台词）
        assert panel._dialogue_box.isHidden(), '没有台词的拍还开着台词框'

    def test_an_ordinary_beat_still_shows_the_stage_block(self, taught_window):
        """
        普通台词拍**不变**：仍常显（"说着话顺手 show 一个"是真写法）。

        2026-10-29 用户：「直接完整显示……因为这是基本信息！默认不应当收起」。
        """
        win = taught_window
        _show(win, _idx_of_text(win, '一'))
        panel = win._panel
        assert not panel._dialogue_box.isHidden()
        assert not panel._stage_box.isHidden(), '普通台词拍的舞台块被收起来了'

    def test_a_brand_new_card_keeps_the_typing_box(self, taught_window):
        """
        插一张空卡片：台词框必须留着 —— 那是人要打字的地方。

        收起判定如果只看"有没有台词"，空卡片就会把自己的输入框收掉
        （2026-11-08 实测：`insert_card()` 之后 `_text` 连点都点不到）。
        """
        win = taught_window
        _show(win, 0)
        win.insert_card(after=True)
        panel = win._panel
        assert not panel._dialogue_box.isHidden(), '空卡片的台词框被收起来了'
        assert panel._text.isVisibleTo(panel), '空卡片的台词框不可见（没法打字）'

    def test_a_flow_card_hides_and_the_next_beat_gets_them_back(
            self, make_window, tmp_path):
        """控制流卡全收起；翻到下一张普通卡，两块按**那一拍**放回来。"""
        root = _project(tmp_path, _FLOW_SRC)
        win = make_window(root)
        call_idx = next(i for i, b in enumerate(win.beats)
                        if str(getattr(b, 'raw', '') or '').startswith('call '))
        _show(win, call_idx)
        panel = win._panel
        assert panel._dialogue_box.isHidden()
        assert panel._stage_box.isHidden()
        _show(win, _idx_of_text(win, '调用之后'))
        assert not panel._dialogue_box.isHidden(), '回到普通卡后台词区没回来'
        assert not panel._stage_box.isHidden(), '回到普通卡后舞台块没回来'


# ---------------------------------------------------------------------------
#  B2. 扩展里声明的写法 → 卡片上的语句条与参数格
# ---------------------------------------------------------------------------

class TestStatementDeclaredInTheDialog:

    @pytest.fixture
    def declared_window(self, make_window, tmp_path, monkeypatch):
        root = _project(tmp_path, _DECLARED_SRC)
        win = make_window(root)
        logged = []
        win._log = logged.append

        def driver(dlg):
            _declare_statement(dlg, 'mycue', [('秒数', 'number'), ('内容', 'variable')],
                               label='我的提示')

        _drive_extensions(win, driver, monkeypatch)
        # 声明改的是"认不认得这行字"，已经读进来的拍不会自己重认 ——
        # 所以按状态栏那句"重新导入那几章"重新导入一次（真路，不构造 Beat）。
        win.load_script(str(win._session.script_path), goto_card=False)
        win._logs = logged
        _show(win, _idx_of(win, 'mycue'))
        return win

    def test_declaring_a_new_statement_says_to_reimport(self, declared_window):
        """存完必须说一声"这一章要重新导入" —— 否则卡片没反应，人只会以为坏了。"""
        said = ' '.join(str(x) for x in declared_window._logs)
        assert '重新导入' in said, said

    def test_the_card_names_the_statement_and_its_columns(self, declared_window):
        win = declared_window
        panel = win._panel
        labels = _labels(panel)
        assert any('◆ mycue' in t and '我的提示' in t for t in labels), labels
        assert any('原文：mycue 3 "x"' in t for t in labels), labels
        assert '秒数' in labels and '内容' in labels, labels
        assert _cell(panel, '秒数')[0] and _cell(panel, '内容')[0]

    def test_editing_a_column_rewrites_only_that_token(self, declared_window):
        """改「内容」→ 那一行只换那一段；别的拍、别的字一个都不动。"""
        win = declared_window
        panel = win._panel
        _cell(panel, '内容')[1].setText('新内容')
        win.save_current_beat()
        out = _export(win)
        assert 'mycue 3 新内容' in out, out
        assert 'mycue 3 "x"' not in out, out

    def test_a_taught_statement_reaches_the_card_too(self, make_window, tmp_path):
        """
        「自定义语句」窗教出来的写法（真控件）—— 卡片上同样要认它。

        与扩展窗口那条的区别只是"写进哪个文件"，进卡片的路是同一条
        （`StatementRegistry` → `spec_columns` → 参数格）。
        """
        root = _project(tmp_path, _DECLARED_SRC)
        _teach(root, 'mycue', 'mycue 3 "x"')
        clear_cache()
        win = make_window(root)
        _show(win, _idx_of(win, 'mycue'))
        panel = win._panel
        assert any('◆ mycue' in t for t in _labels(panel)), _labels(panel)
        assert panel._spec_widgets, '教过的写法没有参数格（卡片不认识它）'

    def test_saving_without_editing_keeps_the_statement_name(self, declared_window):
        """
        一格都不改、直接按保存 —— 那一行必须**逐字节**不变。

        扩展窗口里声明的写法没有"关键字格"（动作名不在格子里）：折回头行时
        动作名要先摘成前缀、折完再补回来。而"值没变"那一路从前**不补前缀**
        （补的条件是 `folded != fold_base`），于是什么都没改也会把 `mycue`
        弄丢 —— `mycue 3 "x"` → `3 "x"`，导出还报"成功"。
        """
        win = declared_window
        before = _export(win)
        win.save_current_beat()
        assert _export(win) == before, _export(win)
        idx = _idx_of(win, 'mycue')
        assert str(getattr(win.beats[idx], 'spec_name', '')) == 'mycue'

    def test_the_statement_is_still_recognized_after_saving(self, declared_window):
        """存一次再读：仍是声明过的那条写法（不是"不认识的整行"）。"""
        win = declared_window
        win.save_current_beat()
        idx = _idx_of(win, 'mycue')
        assert str(getattr(win.beats[idx], 'spec_name', '')) == 'mycue'


# ---------------------------------------------------------------------------
#  C. 映射 → 卡片上的那格人话
# ---------------------------------------------------------------------------

class TestMappingOnTheCard:

    @pytest.fixture
    def mapped(self, make_window, tmp_path):
        root = _project(tmp_path, _MUSIC_SRC)
        _map_music(root)
        clear_cache()
        win = make_window(root)
        _show(win, 0)
        return win

    def test_the_music_cell_shows_the_mapped_name_and_keeps_the_file(self, mapped):
        """
        屏幕上是 `18 战斗`，写回去的必须还是 `bgm/a.mp3`
        （2026-10-31 用户："选出来写进剧本的仍是 `bgm/嗯？.mp3`"）。
        """
        win = mapped
        panel = win._panel
        combo = panel._widgets['BGM']
        assert combo.currentText() == '18 战斗', combo.currentText()
        assert panel._get_value('BGM') == '[音乐] bgm/a.mp3', \
            panel._get_value('BGM')

    def test_saving_the_card_does_not_write_the_display_name(self, mapped):
        """存一次 → 剧本逐字节不变；"18 战斗"一个字都不许进脚本。"""
        win = mapped
        win.save_current_beat()
        out = _export(win)
        assert '18 战斗' not in out, out
        assert 'play music "bgm/a.mp3"' in out, out

    def test_the_display_name_survives_a_field_point(self, make_window,
                                                     tmp_path, monkeypatch):
        """混：映射在场时再存一条拍字段扩展点 —— 人话不许被重建冲掉。"""
        root = _project(tmp_path, _MUSIC_SRC)
        _map_music(root)
        clear_cache()
        win = make_window(root)
        _show(win, 0)
        panel = win._panel

        def driver(dlg):
            _field_point(dlg, '舞台动作', 'Stage', kind='code', label='舞台动作')

        _drive_extensions(win, driver, monkeypatch)
        assert panel._widgets['BGM'].currentText() == '18 战斗'
        assert panel._get_value('BGM') == '[音乐] bgm/a.mp3'


# ---------------------------------------------------------------------------
#  D. 三样同时在场（一张卡上）
# ---------------------------------------------------------------------------

class TestAllThreeInOneCard:

    @pytest.fixture
    def everything(self, make_window, tmp_path, monkeypatch):
        """映射 + 项目里声明过的写法 + 拍字段扩展点，全在一张卡上。"""
        root = _project(tmp_path, _MUSIC_SRC, rules={'statements': [_MYCUE]})
        _map_music(root)
        clear_cache()
        win = make_window(root)

        def driver(dlg):
            _field_point(dlg, '舞台动作', 'Stage', kind='code', label='舞台动作')

        _drive_extensions(win, driver, monkeypatch)
        _show(win, _idx_of(win, 'mycue'))
        return win

    def test_one_card_shows_all_three_without_crossing(self, everything):
        """
        一张 `mycue` 卡上同时要有：语句条、参数格、音乐格里的中文名、
        高级区里被改成「舞台动作」的那格 —— 而且各自不串。
        """
        win = everything
        panel = win._panel
        labels = _labels(panel)
        assert any('◆ mycue' in t for t in labels), labels
        assert '秒数' in labels and '内容' in labels, labels
        assert '舞台动作' in _labels(panel._adv_host), _labels(panel._adv_host)
        # 这一拍没写音乐（沿用上一拍）：屏幕上仍翻成人话，但**不许**变成
        # 这一拍自己的值（`stage_state` 给的是资产 id，`_drop_inherited`
        # 按同一个串比较 —— 两边差一个字符就会凭空多一条 `play music`）。
        assert panel._widgets['BGM'].currentText() == '18 战斗'
        assert panel.collect_fields().get('BGM') == '', \
            '沿用的音乐被当成这一拍自己的了'

    def test_the_three_features_reach_the_product_together(self, everything):
        """改一格参数 → 导出：语句那一行变了、音乐行没被复制、人话没进脚本。"""
        win = everything
        panel = win._panel
        _cell(panel, '内容')[1].setText('新内容')
        win.save_current_beat()
        out = _export(win)
        assert 'mycue 3 新内容' in out, out
        assert out.count('play music "bgm/a.mp3"') == 1, out
        assert '18 战斗' not in out, out
        assert '舞台动作' not in out, out

    def test_a_second_window_shows_the_same_card(self, everything, make_window):
        """关掉重开（另一个窗口）：卡片上的样子必须一样（真源在磁盘上）。"""
        everything.save_current_beat()
        root = Path(everything._project_root)
        again = make_window(root)
        _show(again, _idx_of(again, 'mycue'))
        panel = again._panel
        assert panel._widgets['BGM'].currentText() == '18 战斗'
        assert '舞台动作' in _labels(panel._adv_host)
        assert '秒数' in _labels(panel)
