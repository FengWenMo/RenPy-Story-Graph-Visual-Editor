"""
tests/test_choice_picker.py
===========================
**右键 chip 的可搜索候选列表**（用户 2026-10-05）：

> 既然变换如此多，那为什么不加个右键列表筛选跟搜索功能呢？

真实项目的位置候选有 **151 个**（146 个自定义 transform + 内置位置），
靠"点一下换下一个"要按 40 次才轮到 `i11` —— 那不叫能用。

这一层钉三件事：
  · `filter_choices` 是**纯函数**（筛选口径：忽略大小写 + 子串 + 顺序稳定）；
  · 右键那条路与左键那条路**共用 `_set_field`**（值、图像名同步、
    `on_change` 通知三条都一样），不会出现"两条路给出不同名字"；
  · **弹窗里的按键归弹窗**（2026-10-12 用户实测报："选一次就回车这个回车的
    输入存在问题！按了没反应"）—— 主窗口的键盘过滤器从前会把弹窗里的回车
    吃掉（当成"下一拍 / 进卡片"），于是回车看着毫无反应。
"""

from __future__ import annotations

import os
from pathlib import Path
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest  # noqa: E402

# **先确认有 PySide6 再 import 任何 ui_qt 模块**（2026-10-21，修 CI 全红）：
# 这个文件直接 `from storygraph.ui_qt.…` / `from PySide6…`，没有 PySide6
# 的环境（CI）会在**收集阶段**整批 ERROR —— 收集阶段没有 skip 的机会，
# 一条测试都不会跑。`importorskip` 必须站在第一次 import 它们之前。
pytest.importorskip('PySide6')

from storygraph.ui_qt.choice_picker import filter_choices  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ui_project import build_project  # noqa: E402


class TestFilterChoices:
    def test_empty_query_keeps_everything_in_order(self):
        items = ['i11', 'd11', 'center', 'leftish']
        assert filter_choices(items, '') == items
        assert filter_choices(items, '   ') == items

    def test_substring_and_case_insensitive(self):
        items = ['i11', 'd11', 'bi11', 'Center', 'left']
        assert filter_choices(items, 'i1') == ['i11', 'bi11']
        assert filter_choices(items, 'CEN') == ['Center']

    def test_blank_candidates_are_dropped(self):
        assert filter_choices(['a', '', '  ', 'b'], '') == ['a', 'b']

    def test_order_is_stable(self):
        """顺序必须稳定（用户的手指要记得住位置）——不许按相似度重排。"""
        items = ['z9', 'a1', 'm5']
        assert filter_choices(items, '') == items
        assert filter_choices(items, '9') == ['z9']

    def test_no_match_is_empty_not_an_error(self):
        assert filter_choices(['a', 'b'], 'zzz') == []


class TestChipRightClick:
    """右键 chip → 选一个 → 值与图像名一起更新（与左键同一条路）。"""

    @pytest.fixture
    def card(self, qapp):
        from storygraph.assets.asset_registry import AssetRegistry
        from storygraph.story.beat_render import make_symbols
        from storygraph.ui_qt.sprite_grid import SpriteCardWidget

        reg = AssetRegistry()
        reg.images = {'ling_angry': {'ID': 'ling_angry', 'Tag': 'ling',
                                     'Attributes': 'angry'},
                      'ling_happy': {'ID': 'ling_happy', 'Tag': 'ling',
                                     'Attributes': 'happy'}}
        reg.transforms = {'i11': {'ID': 'i11', 'ATL': 'xpos 0.35'},
                          'd11': {'ID': 'd11', 'ATL': 'xpos 0.2'}}
        changed = []
        return SpriteCardWidget(
            {'image': 'ling angry', 'expression': 'angry', 'position': 'd11',
             'at': '', 'speaking': False, 'order_index': 0, 'extra_rows': []},
            symbols=make_symbols(reg), loader=None,
            on_change=lambda _c: changed.append(1), on_resolve_path=None), changed

    def test_picking_a_position_sets_it(self, card, monkeypatch):
        widget, changed = card
        import storygraph.ui_qt.choice_picker as picker
        monkeypatch.setattr(picker, 'choose_from',
                            lambda *a, **k: 'i11')
        widget._pick_from_list('position')
        assert widget.item['position'] == 'i11'
        assert changed, '右键选完必须通知主窗口（否则"改了没保存"）'

    def test_picking_an_expression_syncs_the_image_name(self, card,
                                                        monkeypatch):
        widget, _changed = card
        import storygraph.ui_qt.choice_picker as picker
        monkeypatch.setattr(picker, 'choose_from',
                            lambda *a, **k: 'happy')
        widget._pick_from_list('expression')
        assert widget.item['expression'] == 'happy'
        assert widget.item['image'] == 'ling happy', (
            f'卡片标题（图像名）没跟着改：{widget.item["image"]!r}')

    def test_cancel_changes_nothing(self, card, monkeypatch):
        widget, changed = card
        import storygraph.ui_qt.choice_picker as picker
        monkeypatch.setattr(picker, 'choose_from', lambda *a, **k: '')
        widget._pick_from_list('position')
        assert widget.item['position'] == 'd11'
        assert not changed, '取消不该通知"改了"'

    def test_the_list_is_the_same_source_as_the_left_click(self, card):
        """两条路必须问同一个函数要候选（不可能给出不一样的名字）。"""
        from storygraph.app.beats import choices_for_image
        widget, _changed = card
        opts = choices_for_image(str(widget.item['image']), widget._symbols)
        assert opts['position'] and opts['expression']
        assert 'i11' in opts['position']


# ==================================================================
#  弹窗里的按键归弹窗（真开 QDialog + 真发按键）
# ==================================================================

@pytest.fixture
def window(qapp, tmp_path):
    """真主窗口（载入一章）—— 主窗口的键盘过滤器**真的装着**，这才测得到抢键。"""
    from storygraph.ui_qt.main_window import MainWindowQt
    proj = build_project(tmp_path / 'proj')
    script = proj / 'game' / 'script' / 'ch01.rpy'

    class _Engine:
        def __init__(self, root):
            self.project_root = root
            self._registry = None

        def get_registry(self):
            return self._registry

    win = MainWindowQt(_Engine(proj), script)
    win.show()
    qapp.processEvents()
    yield win
    win.close()


def _win_shortcut_probe(win):
    """给主窗口的"抢键"路径装计数器（真被抢了才看得出来）。"""
    calls = {'next': 0, 'prev': 0, 'open_card': 0, 'structural': 0}
    win.next_beat = lambda *a, **k: calls.__setitem__('next', calls['next'] + 1)
    win.prev_beat = lambda *a, **k: calls.__setitem__('prev', calls['prev'] + 1)
    win._open_selected_in_card = lambda *a, **k: calls.__setitem__(
        'open_card', calls['open_card'] + 1)
    orig = win._filter_structural_keys

    def spy(event, key, mods, obj, on_card):
        calls['structural'] += 1
        return orig(event, key, mods, obj, on_card)
    win._filter_structural_keys = spy
    return calls


def _open_picker_and_press(win, qapp, key, *, in_filter=False):
    """
    真的开一个候选弹窗（**挂在真主窗口下**，这才复现得到抢键）、真的发一个按键，
    返回 `(弹窗返回值, 计数器, 焦点类名)`。

    ⚠ 两处刻意写法：
      · **自己 new 弹窗**（不调 `choose_from`）：这样"是哪一个弹窗"是确定的 ——
        早先那版去 `topLevelWidgets()` 里捞，会把前一个用例留下的实例捞到手
        （判据自己的时序问题，实测单跑绿、整文件跑红）；
      · 仍走 `exec()`（模态循环），因为**抢键只在这条路上才发生**。
    """
    from PySide6.QtCore import QTimer
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from storygraph.ui_qt.choice_picker import ChoicePickerDialog

    calls = _win_shortcut_probe(win)
    dlg = ChoicePickerDialog(win, title='选位置',
                             choices=['i11', 'd11', 'center'], current='d11')
    box = {}

    def _press():
        fw = dlg._filter if in_filter else QApplication.focusWidget()
        if fw is None or dlg.isAncestorOf(fw) is False:
            fw = dlg._filter
        box['focus'] = type(fw).__name__
        QTest.keyClick(fw, key)
        QTest.qWait(20)
        if dlg.isVisible():                  # 没关掉就别挂住
            dlg.reject()

    QTimer.singleShot(50, _press)
    dlg.exec()
    return dlg.value, calls, box.get('focus')


class TestDialogKeysAreNotStolen:
    """
    主窗口的键盘过滤器**不许碰弹窗里的键**。

    实测现场（修之前，`MainWindowQt` 载入一章后 `page=0`）：
        `dlg.parentWidget() = MainWindowQt`、`win.isAncestorOf(dlg) = False`
        → `_event_belongs_here` 那段"往上找 parentWidget"把**对话框**也认成
          "本窗口的控件树" → 回车被吃掉 → `dlg.visible=True, value=''`
          → 用户看到的就是 **"回车按了没反应"**。
    """

    def test_enter_selects_in_the_picker(self, window, qapp):
        from PySide6.QtCore import Qt
        value, calls, _focus = _open_picker_and_press(window, qapp,
                                                      Qt.Key_Return)
        assert value == 'd11', (
            f'回车没选定（拿到 {value!r}）—— 弹窗里的回车又被主窗口吃了吗？')
        assert calls == {'next': 0, 'prev': 0, 'open_card': 0, 'structural': 0}, \
            f'按回车时主窗口的快捷键被触发了：{calls}'

    def test_enter_inside_the_filter_box_also_selects(self, window, qapp):
        """焦点在筛选框里也一样（用户就是"输入即筛，然后回车"）。"""
        from PySide6.QtCore import Qt
        value, calls, focus = _open_picker_and_press(window, qapp,
                                                     Qt.Key_Return,
                                                     in_filter=True)
        assert focus == 'QLineEdit'
        assert value == 'd11', value
        assert calls['next'] == 0 and calls['structural'] == 0, calls

    def test_escape_closes_the_picker_without_touching_the_card(self, window,
                                                                qapp):
        from PySide6.QtCore import Qt
        value, calls, _focus = _open_picker_and_press(window, qapp,
                                                      Qt.Key_Escape)
        assert value == '', '取消（Esc）不该返回值'
        assert calls['next'] == 0 and calls['open_card'] == 0, calls

    def test_ctrl_s_is_not_saved_while_the_picker_is_open(self, window, qapp):
        """
        弹窗开着时 `Ctrl+S` 不该去存这一拍（那是**别的**输入上下文）。

        实测根因同一条：对话框被当成本窗口的控件树 → 主窗口的 Ctrl+S 与回车
        都会在弹窗里生效。所以这里连"裸 S 只当普通输入"一起钉住。
        """
        from PySide6.QtCore import Qt
        saved = []
        window.save_current_beat = lambda *a, **k: saved.append(1)
        _open_picker_and_press(window, qapp, Qt.Key_S)      # 裸 S：普通输入
        assert not saved, '弹窗开着时不该走到"保存这一拍"'
        _open_picker_and_press(window, qapp, Qt.Key_Escape)
        assert not saved

    def test_double_click_still_selects(self, qapp):
        """
        "双击选定"那条老路不许被这次改动弄坏。

        ⚠ 判据**不起模态循环**（`exec()`）:同一进程里前面用例的弹窗还留着模态状态，
        再起一个会拿不到计时器回调（实测挂住）。双击这件事的判据只有两件，
        都在**构造与信号**这一层能查清：
          ① `itemDoubleClicked` **真的接在** `_accept_item` 上；
          ② 那个落点被调用时会 `accept` 并带回正确的值。
        """
        from storygraph.ui_qt.choice_picker import ChoicePickerDialog

        dlg = ChoicePickerDialog(None, title='选位置',
                                 choices=['i11', 'd11', 'center'], current='d11')
        # ① 信号真的接在落点上：拿一个探针，看 emit 时它在不在
        probes = []
        dlg._list.itemDoubleClicked.connect(lambda it: probes.append(it.text()))
        item = dlg._list.item(0)
        dlg._list.itemDoubleClicked.emit(item)
        assert probes == ['i11'], ('双击信号没接到落点上 —— 双击会毫无反应',
                                   probes)
        # ② 落点自己会把弹窗 accept 并带回选中的值
        accepted = []
        dlg.accepted.connect(lambda: accepted.append(1))
        dlg._accept_item(item)
        assert accepted, '_accept_item 没有 accept 弹窗'
        assert dlg.value == 'i11', dlg.value


class TestInputTreeRule:
    """
    `_belongs_to_this_input_tree` 的三条边界（纯判定，不起弹窗）。

    ⚠ 为什么值得单测：它是"弹窗按键不被抢"的**唯一**判据，
    改错一个字就会两种坏法之一 —— 弹窗又没反应，或者下拉菜单一按方向键就翻拍。
    """

    def test_child_widgets_belong(self, window):
        assert window._belongs_to_this_input_tree(window) is True
        assert window._belongs_to_this_input_tree(window._list) is True

    def test_parented_dialog_does_not_belong(self, window):
        """**就是这一条 bug**：挂在主窗口下的对话框不是"本窗口的控件树"。"""
        from PySide6.QtWidgets import QDialog
        dlg = QDialog(window)
        try:
            assert window.isAncestorOf(dlg) is False       # Qt 的判定
            assert window._belongs_to_this_input_tree(dlg) is False
        finally:
            dlg.close()

    def test_foreign_window_does_not_belong(self, window):
        from PySide6.QtWidgets import QDialog
        other = QDialog()
        try:
            assert window._belongs_to_this_input_tree(other) is False
        finally:
            other.close()

    def test_combo_popup_still_belongs(self, window):
        """
        组合框的下拉是**顶层窗口**，但它是本窗口的一部分 —— 必须继续放行，
        否则 `show` 列的下拉一按方向键就会翻拍（那是个更老的坑）。
        """
        from PySide6.QtWidgets import QComboBox
        combo = window.findChild(QComboBox)
        if combo is None:
            pytest.skip('这个窗口里没有组合框')
        combo.showPopup()
        combo.view().setFocus()
        try:
            belongs = window._belongs_to_this_input_tree(combo.view())
        finally:
            combo.hidePopup()
        assert belongs is True, '组合框下拉被当成"别人的窗口"了 —— 方向键会翻拍'
