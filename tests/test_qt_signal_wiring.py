"""
Qt 接线契约：**按钮点下去，真的走到作者写的那个语义**
=======================================================

用户 2026-10-05 报的 bug：

> 「插入卡片」与「插到前面」效果现在完全一样，插入卡片应当在当前我选中的
> 卡片条目下往下一条插入，而不是往上一条插入。

根因**不在**语义层（`EditorSession.insert_beat(idx, after=...)` 一直是对的，
`test_editor_session_behavior` 也一直是绿的），而在**接线**：

    QPushButton.clicked 的签名是 clicked(bool checked = False)
    self._insert_btn.clicked.connect(self.insert_card)      # insert_card(after=True)
    # → PySide 把这个 bool 当第一个位置参数：实际调 insert_card(after=False)

于是两个按钮都变成"插到前面"。同类（同一个信号、同样的默认值不等于 False）
还有四处，一并修了：

    save_layout(target=None)        → 点了不弹「另存为」，拿 False 当路径
    load_layout(source=None)        → 点了不弹「打开」，同上
    export_control_flow(target=None)→ 同上
    add_node(at=None) / add_edge(from_id=None)   ← 走工具栏 act(...) 那条路

**为什么原来测不出来**：`test_ui_qt` 里那两条是**直接调方法**
（`window.insert_card(after=True)`），根本没经过信号 —— 缺陷在接线处，
用例绕开了接线。所以这个文件只做一件事：**点真的按钮 / 触发真的 QAction**。

（这是本仓库第三次踩"测试绿了 ≠ 缺陷被钉住"：前两次是单测停在
`beats_from_parse_result()`、以及空 label 用例不走 Excel。判据必须走
**缺陷真正发生的那条路径**。）
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pytest.importorskip('PySide6')


# ---------------------------------------------------------------------------
#  工具
# ---------------------------------------------------------------------------

def _graph_with_two_nodes(window):
    """给窗口装一张有两个模块的图（几条用例都需要"非空图"）。"""
    from storygraph.graph.models import Graph

    g = Graph()
    a = g.add_node('a', 0, 0)
    b = g.add_node('b', 900, 600)
    window._set_graph(g)
    return g, a, b


def _action(window, text: str):
    """按文字找工具栏动作（走的是 `act(...)` 加进去的那批）。"""
    for a in window._tb_graph.actions():
        if text in a.text():
            return a
    raise AssertionError(f'工具栏里没有 {text!r}：'
                         f'{[x.text() for x in window._tb_graph.actions()]}')


def _patch_dialog(monkeypatch, cls_name, **attrs):
    """把某个对话框换成"点确定"的桩（与 `test_ui_qt` 里那份同款）。"""
    from PySide6.QtWidgets import QDialog

    from storygraph.ui_qt import dialogs as D

    cls = getattr(D, cls_name)

    def fake_init(self, *a, **k):
        for key, value in attrs.items():
            setattr(self, key, value)

    monkeypatch.setattr(cls, '__init__', fake_init)
    monkeypatch.setattr(cls, 'exec', lambda self: QDialog.Accepted)


def _patch_file_dialog(monkeypatch, *, save=None, open_=None):
    """替换文件对话框，并记下"到底弹没弹"。"""
    from PySide6.QtWidgets import QFileDialog

    calls = {'save': 0, 'open': 0}

    def _save(*a, **k):
        calls['save'] += 1
        return str(save), 'JSON 文件 (*.json)'

    def _open(*a, **k):
        calls['open'] += 1
        return str(open_), 'JSON 文件 (*.json)'

    monkeypatch.setattr(QFileDialog, 'getSaveFileName', staticmethod(_save))
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', staticmethod(_open))
    return calls


# ---------------------------------------------------------------------------
#  1. 用户报的那一条：两个插入按钮必须真的不一样
# ---------------------------------------------------------------------------

class TestInsertButtons:

    def test_insert_card_goes_after_the_current_card(self, ui_window):
        """「＋ 插入卡片」= 插在**当前这张**之后（用户明确要求的方向）。"""
        w = ui_window
        w._set_page(w.PAGE_CARD)
        w._show_index(2)
        before = [b.text for b in w.beats]

        w._insert_btn.click()                 # ← 走真的信号（Qt 会带一个 bool）

        after = [b.text for b in w.beats]
        assert w._loaded_idx == 3, (
            f'「插入卡片」应落在第 3 张（当前是 2）—— 实际 {w._loaded_idx + 1} 张')
        assert len(after) == len(before) + 1
        assert after[:3] == before[:3], '前 3 张的内容变了'
        assert after[3] == '', '新卡应该是**空的**'
        assert after[4:] == before[3:], '原有卡片顺序被挪动了'

    def test_insert_before_goes_before_the_current_card(self, ui_window):
        """「＋ 插到前面」= 插在**当前这张**之前。"""
        w = ui_window
        w._set_page(w.PAGE_CARD)
        w._show_index(2)
        before = [b.text for b in w.beats]

        w._insert_before_btn.click()

        after = [b.text for b in w.beats]
        assert w._loaded_idx == 2, (
            f'「插到前面」应落在第 3 张（当前是 2）—— 实际 {w._loaded_idx + 1} 张')
        assert after[:2] == before[:2]
        assert after[2] == ''
        assert after[3:] == before[2:]

    def test_the_two_buttons_are_not_the_same(self, ui_window):
        """
        两条路的结果必须**可区分** —— 这正是用户报的"完全一样"。

        单独一条不够：上两条各自断言了方向，这一条直接把两次点击的落点
        摆在一起比（将来有人把两个按钮接成同一个 lambda，它会红）。
        """
        w = ui_window
        w._set_page(w.PAGE_CARD)

        w._show_index(2)
        w._insert_btn.click()
        after_idx = w._loaded_idx

        w._session.delete_beat(after_idx)
        w._reload_list()
        w._show_index(2)
        w._insert_before_btn.click()
        before_idx = w._loaded_idx

        assert (after_idx, before_idx) == (3, 2), (
            f'两个按钮的落点应当不同（3 / 2），实际 {after_idx} / {before_idx}'
            f' —— "插入卡片"很可能又被 Qt 的 bool 顶成了 after=False')

    def test_inserted_card_keeps_its_own_content(self, ui_window):
        """插出来的是**空卡**：不许把上一拍的背景/音乐复制过来。"""
        w = ui_window
        w._set_page(w.PAGE_CARD)
        w._show_index(0)
        w._insert_btn.click()
        new = w.beats[w._loaded_idx]
        assert not (new.scene or new.show or new.bgm or new.sfx or new.voice), \
            f'新卡带上了别人的舞台/音频：{new.to_row()}'


# ---------------------------------------------------------------------------
#  2. 同一类缺陷：另外 5 个入口（点了没反应 / 拿 False 当路径）
# ---------------------------------------------------------------------------

class TestOtherSlotsAreNotFedTheBool:
    """
    这些槽的第一个参数是 `None` 默认值（"没给就弹对话框/自动算"）。
    Qt 喂进来 `False` 之后，`is None` 判不成立 → 对话框不弹、`False` 当路径。

    实测（修之前）：`save_layout(False)` → `Path(False)` →
    `TypeError: argument should be a str or an os.PathLike object ... not 'bool'`，
    用户看到的是"点了一下，什么都没发生"。
    """

    def test_save_layout_button_writes_a_file(self, ui_window, tmp_path,
                                              monkeypatch):
        w = ui_window
        _graph_with_two_nodes(w)
        target = tmp_path / 'layout.json'
        calls = _patch_file_dialog(monkeypatch, save=target)

        w._save_layout_btn.click()

        assert calls['save'] == 1, (
            '点了「保存布局」却没弹「另存为」—— 说明槽收到的第一个参数不是 '
            'None（多半是 Qt 的 bool）')
        assert target.exists(), '另存为之后文件没写出来'

    def test_load_layout_button_reads_a_file(self, ui_window, tmp_path,
                                             monkeypatch):
        w = ui_window
        _graph_with_two_nodes(w)
        target = tmp_path / 'layout.json'
        v = _patch_file_dialog(monkeypatch, save=target)
        w._save_layout_btn.click()
        assert v['save'] == 1 and target.exists()

        w._set_graph(type(w.graph)())          # 先清空，才能看出"读回来了"
        calls = _patch_file_dialog(monkeypatch, open_=target)
        w._load_layout_btn.click()

        assert calls['open'] == 1, '点了「加载布局」却没弹「打开」'
        assert len(w.graph.nodes) == 2, '布局没读回图上'

    def test_export_control_flow_button_writes_a_file(self, ui_window, tmp_path,
                                                      monkeypatch):
        w = ui_window
        g, a, b = _graph_with_two_nodes(w)
        g.add_edge(a, b, 'jump')
        target = tmp_path / 'flow.rpy'
        calls = _patch_file_dialog(monkeypatch, save=target)

        w._export_flow_btn.click()

        assert calls['save'] == 1, '点了「导出控制流」却没弹「保存为」'
        assert target.exists(), '控制流没写出来'

    def test_add_node_action_adds_a_node(self, ui_window, monkeypatch):
        """工具栏「➕ 新章节」：`add_node(at=None)` 收到 False 会 `float(False[0])` 炸。"""
        w = ui_window
        _graph_with_two_nodes(w)
        _patch_dialog(monkeypatch, 'AddNodeDialog', name='ch99', operations='')

        _action(w, '新章节').trigger()

        labels = [getattr(n, 'label_name', '') for n in w.graph.nodes.values()]
        assert 'ch99' in labels, f'新章节没进图：{labels}'

    def test_add_edge_action_adds_an_edge(self, ui_window, monkeypatch):
        """
        工具栏「🔗 连线」：`add_edge(from_id=None)` 收到 False 会拿 False 当节点 id。

        这里**连对话框收到的实参一起断言**（`from_id is None`）：
        只断言"连线进图了"是不够的 —— 桩会把 from_id 覆盖成 a，
        那样即使槽收到 `False` 也照样绿（变异验证抓到的假绿）。
        `None` 才代表走了"用画布上选中的那个模块"这条正常分支。
        """
        from PySide6.QtWidgets import QDialog

        from storygraph.ui_qt import dialogs as D

        w = ui_window
        g, a, b = _graph_with_two_nodes(w)
        seen = {}

        def fake_init(self, *args, **kwargs):
            seen.update(kwargs)
            self.from_id, self.to_id = a, b
            self.etype, self.condition, self.option_text = 'jump', '', ''

        monkeypatch.setattr(D.AddEdgeDialog, '__init__', fake_init)
        monkeypatch.setattr(D.AddEdgeDialog, 'exec',
                            lambda self: QDialog.Accepted)

        _action(w, '连线').trigger()

        assert seen.get('from_id', 'missing') is None, (
            f'槽收到的 from_id 应当是 None（=用画布选中的模块），'
            f'实际 {seen.get("from_id")!r} —— 多半是 Qt 的 bool 顶进来了')
        assert len(w.graph.edges) == 1, f'连线没进图：{w.graph.edges}'


# ---------------------------------------------------------------------------
#  3. 导出之后列表必须跟着磁盘走（原地写回会重新对齐结构）
# ---------------------------------------------------------------------------

class TestExportKeepsTheUiInSync:
    """
    **原地导出之后，卡片列表必须重新对齐**（2026-10-05）。

    为什么：`EditorSession.export` 写回"我们载入的那个文件"之后会重新解析它
    （内存 := 磁盘，见 `session_export._reload_after_inplace_write`）——
    拍的**粒度**可能变：一张只有注释/代码的卡会并进后面那张。
    列表不刷新的话，界面还显示旧的拍，用户点哪一张都对不上，
    `_loaded_idx` 还可能越界。
    """

    def test_list_follows_the_new_beat_count(self, ui_window):
        w = ui_window
        w._set_page(w.PAGE_CARD)
        w._reload_list()
        before = w._list.count()
        assert before >= 2, f'样本太小（{before} 张），换一个 _ui_project 样本'

        # 模拟"导出之后只剩一张拍"（解析器把注释卡并进了后面那张）
        del w._session.beats[1:]
        w._session.mark_loaded(before - 1)         # 原来指着最后一张（现在越界）

        w.resync_after_export({'count': len(w._session.beats)})

        assert w._list.count() == 1, (
            f'列表没跟着刷新：{w._list.count()} 张 vs 会话 {len(w._session)} 张')
        assert w._loaded_idx == 0, '选择位置该被夹到合法范围里'

    def test_unchanged_count_does_not_disturb_the_selection(self, ui_window):
        """拍数没变时**不许**动列表（别打扰最常见的"改一句再导出"）。"""
        w = ui_window
        w._set_page(w.PAGE_CARD)
        w._reload_list()
        w._list.setCurrentRow(0)                   # 用户手选的那一行
        idx = w._loaded_idx                        # 选中之后的位置（作为基准）
        w.resync_after_export({'count': len(w._session.beats)})
        assert w._list.currentRow() == 0, '拍数没变却重建了列表（选择状态被清）'
        assert w._loaded_idx == idx


# ---------------------------------------------------------------------------
#  4. 兜底：就算 bool 真被送进来，也不许它变成参数
# ---------------------------------------------------------------------------

class TestQtBoolIsNeverAnArgument:
    """
    静态判据（`audit_qt_signal_slots.py`）防"新写的裸 connect"；
    这里防"任何一条路径把 bool 送进槽"。

    为什么两层都要（2026-10-04 的真实事故）：仓库根目录里出现过一个名叫
    `False` 的文件（178 字节的控制流脚本）—— 按钮
    `self._export_flow_btn.clicked.connect(self.export_control_flow)`
    收到 `clicked` 发的 `bool`，它成了**第一个位置参数** → `target=False` →
    槽里的判据是 `if target is None:`，`False is None` 是 False →
    "没给路径就弹另存为"那条分支**被绕过** → `False` 当路径写盘。
    """

    CONTEXTS = ('ui_qt.save_layout', 'ui_qt.load_layout',
                'ui_qt.export_control_flow', 'ui_qt.add_node',
                'ui_qt.add_edge')

    def test_bool_becomes_none_and_leaves_a_trace(self):
        from storygraph.core import safeguard
        from storygraph.ui_qt.qt_args import drop_qt_bool

        safeguard.clear()
        assert drop_qt_bool(False, context='ui_qt.add_node') is None
        assert drop_qt_bool(True, context='ui_qt.add_edge') is None
        ctx = safeguard.contexts()
        assert 'ui_qt.add_node' in ctx and 'ui_qt.add_edge' in ctx, ctx
        # 真路径/真坐标**必须原样过**（别把正常值也吃了）
        assert drop_qt_bool('a.rpy', context='x') == 'a.rpy'
        assert drop_qt_bool((1, 2), context='x') == (1, 2)
        assert drop_qt_bool(None, context='x') is None
        # 非布尔的错类型照旧往下走（该炸就炸，别把 bug 藏起来）
        assert drop_qt_bool(7, context='x') == 7

    def test_every_slot_uses_the_guard(self):
        """5 个槽的入口都得过这道兜底（漏一个就等于没修）。"""
        root = ROOT / 'storygraph' / 'ui_qt'
        src = ((root / 'window_graph_ops.py').read_text(encoding='utf-8')
               + (root / 'window_nodes.py').read_text(encoding='utf-8'))
        missing = [c for c in self.CONTEXTS if f"context='{c}'" not in src]
        assert not missing, f'这些槽没有兜底：{missing}'

    def test_export_flow_with_a_bool_opens_the_dialog(
            self, ui_window, monkeypatch):
        """
        真行为：`export_control_flow(False)`（= 裸 connect 的等价物）
        **必须照常弹对话框**，而不是把 `False` 当路径。

        （修之前这条路会绕过对话框，一路写到工作目录里的 `False`；
        会话层 2026-10-05 加了 `isinstance(target, (str, Path))` 硬护栏，
        所以现在即使绕过也写不出垃圾文件 —— 但"对话框被绕过"仍然不对。）
        """
        from PySide6.QtWidgets import QFileDialog

        w = ui_window
        _graph_with_two_nodes(w)
        calls = []
        picked = ROOT / '.pip-tmp' / 'flow_from_bool.rpy'
        monkeypatch.setattr(
            QFileDialog, 'getSaveFileName',
            staticmethod(lambda *a, **k: (calls.append(1),
                                          (str(picked), "Ren'Py (*.rpy)"))[1]))
        w.export_control_flow(False)
        assert calls, '`False` 让对话框被绕过了（那正是 `False` 文件的成因）'

    def test_add_node_with_a_bool_does_not_crash(self, ui_window, monkeypatch):
        """`add_node(at=False)` 以前会在 `float(False[0])` 上抛 TypeError。"""
        w = ui_window
        _graph_with_two_nodes(w)
        _patch_dialog(monkeypatch, 'AddNodeDialog', name='ch_bool',
                      operations='')
        w.add_node(False)                     # 不抛就算过；落点走"视野中央"
        labels = [getattr(n, 'label_name', '') for n in w.graph.nodes.values()]
        assert 'ch_bool' in labels, labels

    def test_add_edge_with_a_bool_falls_back_to_the_selection(
            self, ui_window, monkeypatch):
        """`add_edge(from_id=False)`：`False` 不是节点 id，要退回"画布选中"。"""
        from PySide6.QtWidgets import QDialog

        from storygraph.ui_qt import dialogs as D

        w = ui_window
        _g, a, _b = _graph_with_two_nodes(w)
        seen = {}

        def fake_init(self, *args, **kwargs):
            seen.update(kwargs)
            self.from_id, self.to_id = a, a
            self.etype, self.condition, self.option_text = 'jump', '', ''

        monkeypatch.setattr(D.AddEdgeDialog, '__init__', fake_init)
        monkeypatch.setattr(D.AddEdgeDialog, 'exec',
                            lambda self: QDialog.Accepted)
        w.add_edge(False)
        assert seen.get('from_id', 'missing') is None, (
            f'槽收到的应当是 None（=用画布选中的模块），实际 '
            f'{seen.get("from_id")!r} —— bool 被当成 id 了')


# ---------------------------------------------------------------------------
#  5. 静态守卫：这一类缺陷不许再回来
# ---------------------------------------------------------------------------

class TestWiringAudit:
    """
    行为用例只能覆盖"有按钮的那些入口"。真正防回归的是那条**静态判据**：
    任何 `clicked/triggered.connect(self.槽)`，只要槽的第一个可选参数的默认值
    不是 `False`（Qt 发的就是 `False`），就报出来。

    判据只有**一份**（`tools/audit_qt_signal_slots.py`，交付体检入口之一），
    这里按路径把它载进来跑 —— 不在测试里再抄一遍（同一条判据两处实现，
    迟早一处改了一处没改）。
    """

    def test_no_slot_is_fed_the_clicked_bool(self, capsys):
        spec = importlib.util.spec_from_file_location(
            'audit_qt_signal_slots',
            ROOT / 'tools' / 'audit_qt_signal_slots.py')
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        code = mod.main()
        out = capsys.readouterr().out
        assert code == 0, (
            f'有按钮的接线会把 Qt 的 bool 当成槽的第一个参数：\n{out}\n'
            f'修法：`btn.clicked.connect(lambda: self.slot())`')
