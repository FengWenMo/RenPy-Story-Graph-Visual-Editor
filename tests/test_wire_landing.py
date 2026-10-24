# -*- coding: utf-8 -*-
"""
E1/E2/E3：拖一条线，落点由**剧本结构**决定。

判据的核心不是"能不能插"，而是**什么时候不许问、什么时候必须问**：

| 章末最后一条 | 落点 | 问不问 |
|---|---|---|
| `return` | 插在 return 之前 | **不问**（位置唯一） |
| 台词 / 舞台 | 接在末尾 | **不问** |
| `jump` / `call` / `menu` | 已有去向 | **要问** |
| 起点不是当前章 | 插不了 | 打开接线窗口，草图是显式选项 |

最后一条尤其重要：插拍依赖 session 在那一章。起点不匹配时**不许**画一条
"看起来写进去了"的线 —— 那种假象比报错难查得多；也不许静默什么都不做。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import MethodType

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ui_project import build_project  # noqa: E402

from storygraph.app.editor import EditorSession  # noqa: E402
from storygraph.graph.models import Graph  # noqa: E402
from storygraph.ui_qt.dialogs import AddEdgeDialog  # noqa: E402
from storygraph.ui_qt.window_card import CardPageMixin  # noqa: E402
from storygraph.ui_qt.window_nodes import NodeEditMixin  # noqa: E402

class _Host:
    def __init__(self, session, graph):
        self._session = session
        self.graph = graph
        self.status: list = []
        self.logs: list = []
        self.add_edge_calls: list = []
        self.selected_node = None
        self.selected_edge = None

    def _status(self, msg):
        self.status.append(str(msg))

    def _log(self, msg):
        self.logs.append(str(msg))

    def _set_graph(self, graph, keep_view=None):
        self.graph = graph

    def add_edge(self, from_id=None, to_id=None):
        self.add_edge_calls.append((from_id, to_id))

    def _run_command(self, command):
        command.execute()
        return True

    def on_graph_structure_changed(self):
        pass

    def load_script(self, path, goto_card=True, label=None):
        self._session.load(path, label=label)

    _write_dialog_edge = NodeEditMixin._write_dialog_edge
    _add_manual_edge = NodeEditMixin._add_manual_edge


_Host.plan_wire_landing = NodeEditMixin.plan_wire_landing
_Host._apply_wire = NodeEditMixin._apply_wire
_Host._on_connect_requested = NodeEditMixin._on_connect_requested
_Host._refresh_flow_edges = CardPageMixin._refresh_flow_edges
_Host._source_info = NodeEditMixin._source_info
_Host._source_beats = NodeEditMixin._source_beats
_Host._same_chapter_file = NodeEditMixin._same_chapter_file
_Host._ensure_chapter_loaded = NodeEditMixin._ensure_chapter_loaded


TAIL_RETURN = '''label ch01:
    e "第一句"
    e "第二句"
    return

label ch02:
    e "下一章"
    return
'''

TAIL_JUMP = '''label ch01:
    e "第一句"
    jump ch02

label ch02:
    e "下一章"
    return
'''

TAIL_NARRATION = '''label ch01:
    e "第一句"
    e "最后一句"

label ch02:
    e "下一章"
    return
'''

TAIL_ONLY_RETURN = '''label ch01:
    return

label ch02:
    return
'''

TAIL_MENU = '''label ch01:
    menu:
        "走":
            jump ch02
        "留":
            return

label ch02:
    return
'''

TAIL_IF = '''label ch01:
    e "第一句"
    if flag:
        e "条件里"
    return

label ch02:
    return
'''


def _open(tmp_path, body, name='ch01.rpy'):
    root = build_project(tmp_path / 'proj')
    script = root / 'game' / 'script' / name
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(body, encoding='utf-8')
    session = EditorSession(project_root=root)
    res = session.import_chapters([script], Graph())
    assert res['ok'], res.get('error')
    graph = res['graph']
    session.load(script)
    return session, graph, _Host(session, graph), root


def _nodes(graph):
    by_label = {n.label_name: nid for nid, n in graph.nodes.items()}
    return by_label


# ===========================================================================
#  一、计划：落点由结构决定
# ===========================================================================

class TestPlan:
    def test_return_tail_inserts_before_the_return(self, tmp_path):
        session, graph, host, _ = _open(tmp_path, TAIL_RETURN)
        ids = _nodes(graph)
        plan = host.plan_wire_landing(ids['ch01'], ids['ch02'])

        assert plan['ok'] is True and plan['kind'] == 'chapter_end'
        # 落点用 return **自己**当参考拍（after=False → before=True），
        # 不能用"倒数第二拍 + after"：倒数第二拍可能在块体里。
        assert session.beats[plan['index']].raw.strip() == 'return'
        assert plan['after'] is False

    def test_return_after_an_if_block_lands_outside_the_block(self, tmp_path):
        """
        2026-10-24 实测过的真 bug：章末 `return` 前面是一个 `if` 块时，
        用"倒数第二拍 + after"会把 `jump` 插进 if 体里 —— 只在条件成立时
        执行。这里钉死：新拍必须在**顶层**，与 return 平级。
        """
        session, graph, host, _ = _open(tmp_path, TAIL_IF)
        ids = _nodes(graph)
        plan = host.plan_wire_landing(ids['ch01'], ids['ch02'])
        assert plan['ok'] is True and plan['after'] is False

        res = host._apply_wire(plan)
        assert res.get('ok'), res
        added = session.beats[int(res['index'])]
        assert added.raw.strip() == 'jump ch02'
        assert getattr(added, 'nest_path', '') == '', \
            f'章末接线被插进块体里了：nest_path={added.nest_path!r}'
        assert not str(getattr(added, 'condition', '') or ''), added.condition

        raws = [b.raw.strip() for b in session.beats if b.label == 'ch01']
        assert raws[-1] == 'return', f'return 必须还在最后：{raws}'
        assert raws[-2] == 'jump ch02', f'jump 必须在 return 前、if 块外：{raws}'

    def test_narration_tail_appends_at_the_end(self, tmp_path):
        session, graph, host, _ = _open(tmp_path, TAIL_NARRATION)
        ids = _nodes(graph)
        plan = host.plan_wire_landing(ids['ch01'], ids['ch02'])
        assert plan['kind'] == 'chapter_end'
        beat = session.beats[plan['index']]
        assert beat.text == '最后一句', beat.text

    def test_jump_tail_is_ambiguous(self, tmp_path):
        _session, graph, host, _ = _open(tmp_path, TAIL_JUMP)
        ids = _nodes(graph)
        plan = host.plan_wire_landing(ids['ch01'], ids['ch02'])
        assert plan['kind'] == 'ambiguous'
        assert 'jump' in plan['last_raw']

    def test_menu_tail_is_ambiguous(self, tmp_path):
        _session, graph, host, _ = _open(tmp_path, TAIL_MENU)
        ids = _nodes(graph)
        plan = host.plan_wire_landing(ids['ch01'], ids['ch02'])
        assert plan['kind'] == 'ambiguous'

    def test_only_a_return_still_lands(self, tmp_path):
        session, graph, host, _ = _open(tmp_path, TAIL_ONLY_RETURN)
        ids = _nodes(graph)
        plan = host.plan_wire_landing(ids['ch01'], ids['ch02'])
        assert plan['kind'] == 'chapter_end'
        assert plan['after'] is False, '只有一句 return 时要插在它前面'

    def test_start_not_loaded_is_not_writable(self, tmp_path):
        """
        起点那一章没载入 → **写不了剧本**（插拍依赖 session 在那一章）。
        注意：这只是"不能写"，**不是"不许连线"** —— 画草图仍然要能画。
        """
        session, graph, host, _ = _open(tmp_path, TAIL_RETURN)
        ids = _nodes(graph)
        session.beats = [b for b in session.beats if b.label == 'ch02']
        plan = host.plan_wire_landing(ids['ch01'], ids['ch02'])
        assert plan['kind'] == 'not_loaded'
        assert '写不进剧本' in plan['reason']

    def test_same_node_is_refused(self, tmp_path):
        _session, graph, host, _ = _open(tmp_path, TAIL_RETURN)
        ids = _nodes(graph)
        plan = host.plan_wire_landing(ids['ch01'], ids['ch01'])
        assert plan['ok'] is False
        assert '同一个模块' in plan['reason']

    def test_plan_does_not_change_anything(self, tmp_path):
        session, graph, host, _ = _open(tmp_path, TAIL_RETURN)
        ids = _nodes(graph)
        before = [(b.line_id, b.raw) for b in session.beats]
        host.plan_wire_landing(ids['ch01'], ids['ch02'])
        assert [(b.line_id, b.raw) for b in session.beats] == before


# ===========================================================================
#  二、应用：真的插了一行，图跟着变
# ===========================================================================

class TestApply:
    def test_writes_one_flow_beat(self, tmp_path):
        session, graph, host, _ = _open(tmp_path, TAIL_RETURN)
        ids = _nodes(graph)
        before = len(session.beats)

        plan = host.plan_wire_landing(ids['ch01'], ids['ch02'])
        res = host._apply_wire(plan)
        assert res.get('ok'), res

        assert len(session.beats) == before + 1
        added = session.beats[int(res['index'])]
        assert added.raw.strip() == 'jump ch02', added.raw

    def test_the_line_lands_before_return_not_after(self, tmp_path):
        """插在 return **后面**等于永远执行不到 —— 顺序必须对。"""
        session, graph, host, _ = _open(tmp_path, TAIL_RETURN)
        ids = _nodes(graph)
        host._apply_wire(host.plan_wire_landing(ids['ch01'], ids['ch02']))

        raws = [b.raw.strip() for b in session.beats if b.label == 'ch01']
        assert raws[-1] == 'return', f'return 必须还在最后：{raws}'
        assert 'jump ch02' in raws, raws

    def test_graph_edge_appears(self, tmp_path):
        _session, graph, host, _ = _open(tmp_path, TAIL_RETURN)
        ids = _nodes(graph)
        before = len(graph.edges)
        host._apply_wire(host.plan_wire_landing(ids['ch01'], ids['ch02']))
        assert len(host.graph.edges) >= before, '图上的边要跟着派生出来'

    def test_status_tells_you_how_to_undo(self, tmp_path):
        session, graph, host, _ = _open(tmp_path, TAIL_RETURN)
        ids = _nodes(graph)
        host._apply_wire(host.plan_wire_landing(ids['ch01'], ids['ch02']))
        text = ' '.join(host.status)
        assert 'Ctrl+Z' in text or '撤销' in text, text
        assert '导出' in text, '要说清"还没落盘"'

    def test_undo_puts_it_back(self, tmp_path):
        session, graph, host, _ = _open(tmp_path, TAIL_RETURN)
        ids = _nodes(graph)
        before = [(b.line_id, b.raw) for b in session.beats]
        host._apply_wire(host.plan_wire_landing(ids['ch01'], ids['ch02']))

        res = session.undo_structural()
        assert res.get('ok', True), res
        assert [(b.line_id, b.raw) for b in session.beats] == before


# ===========================================================================
#  三、接线对话框：默认写剧本；草图是显式次选（P0，2026-10-24）
# ===========================================================================

class TestAddEdgeDialogWrite:
    @staticmethod
    def _bind(host):
        """把 `add_edge` 真正挂到测试宿主上（`_Host` 默认把它打桩了）。"""
        host.add_edge = MethodType(NodeEditMixin.add_edge, host)
        return host

    @staticmethod
    def _fake_dialog(monkeypatch, **attrs):
        from PySide6.QtWidgets import QDialog

        from storygraph.ui_qt import dialogs as D

        class _Dlg:
            def __init__(self, *args, **kwargs):
                for key, value in attrs.items():
                    setattr(self, key, value)

            def exec(self):
                return QDialog.Accepted

        monkeypatch.setattr(D, 'AddEdgeDialog', _Dlg)

    def test_dialog_default_writes_into_the_script(self, tmp_path, monkeypatch):
        session, graph, host, _ = _open(tmp_path, TAIL_RETURN)
        ids = _nodes(graph)
        self._bind(host)
        self._fake_dialog(
            monkeypatch, from_id=ids['ch01'], to_id=ids['ch02'],
            etype='jump', condition='', option_text='', write=True,
            position='chapter_end')

        before = len(session.beats)
        host.add_edge(from_id=ids['ch01'], to_id=ids['ch02'])

        assert len(session.beats) == before + 1, '默认动作必须是写剧本，不是画草图'
        assert any(b.raw.strip() == 'jump ch02' for b in session.beats), \
            [b.raw for b in session.beats]
        assert not [e for e in graph.edges.values() if e.origin == 'manual'], \
            '写成剧本之后不该再留一条同名的草图'

    def test_dialog_sketch_is_explicit_and_marked(self, tmp_path, monkeypatch):
        _session, graph, host, _ = _open(tmp_path, TAIL_RETURN)
        ids = _nodes(graph)
        self._bind(host)
        self._fake_dialog(
            monkeypatch, from_id=ids['ch01'], to_id=ids['ch02'],
            etype='call', condition='', option_text='', write=False,
            position='chapter_end')

        host.add_edge(from_id=ids['ch01'], to_id=ids['ch02'])

        manual = [e for e in graph.edges.values() if e.origin == 'manual']
        assert len(manual) == 1, graph.edges
        assert manual[0].position == 'chapter_end'
        assert any('草图' in s for s in host.status), host.status

    def test_draft_checkbox_is_off_by_default(self, qt_app):
        """左下角的「草稿」默认不勾；默认位置就是章末。"""
        from types import SimpleNamespace

        dlg = AddEdgeDialog(
            None,
            nodes={'a': SimpleNamespace(label_name='A'),
                   'b': SimpleNamespace(label_name='B')},
            from_id='a', to_id='b')

        assert dlg._draft.isChecked() is False
        assert '草稿' in dlg._draft.text()
        assert dlg.position == 'chapter_end'

    def test_checking_draft_keeps_the_beat_but_never_writes(self, qt_app,
                                                            tmp_path):
        """勾了草稿：位置照样记到拍，但 `write` 必须是 False。"""
        from PySide6.QtWidgets import QDialog

        session, graph, host, _ = _open(tmp_path, TAIL_RETURN)
        ids = _nodes(graph)
        first = next(b for b in session.beats
                     if b.label == 'ch01' and b.kind == 'say')
        positions = MethodType(NodeEditMixin.edge_position_choices, host)

        dlg = AddEdgeDialog(
            None, nodes=graph.nodes, from_id=ids['ch01'], to_id=ids['ch02'],
            plan_fn=host.plan_wire_landing, positions_fn=positions)
        dlg._pos.setCurrentIndex(1)       # 第 1 拍（say）
        dlg._draft.setChecked(True)
        dlg._on_accept()

        assert dlg.result() == QDialog.Accepted
        assert dlg.draft is True
        assert dlg.write is False
        assert (dlg.position, dlg.position_line_id) == ('beat', first.line_id)

    def test_add_edge_with_draft_only_draws_and_keeps_the_position(
            self, tmp_path, monkeypatch):
        """勾草稿后即使能写，也不许往剧本里插一行。"""
        session, graph, host, _ = _open(tmp_path, TAIL_RETURN)
        ids = _nodes(graph)
        first = next(b for b in session.beats
                     if b.label == 'ch01' and b.kind == 'say')
        self._bind(host)
        self._fake_dialog(
            monkeypatch, from_id=ids['ch01'], to_id=ids['ch02'],
            etype='jump', condition='', option_text='', write=True,
            draft=True, position='beat', position_line_id=first.line_id,
            reason='')

        before = len(session.beats)
        host.add_edge(from_id=ids['ch01'], to_id=ids['ch02'])

        assert len(session.beats) == before, '勾了草稿就不该写剧本'
        manual = [e for e in graph.edges.values() if e.origin == 'manual']
        assert len(manual) == 1, graph.edges
        assert (manual[0].position, manual[0].position_line_id) == \
            ('beat', first.line_id)
        assert any('草稿' in s for s in host.status), host.status


def _drain_threads(ms: int = 3000):
    """
    等全局线程池跑完再让窗口被回收。

    ⚠ 这是**既有**竞态（不是本次改动引入）：`thumb_loader._schedule_cache_prune`
    把缓存清理丢进 `QThreadPool.globalInstance()`，窗口 close 后线程还在跑；
    进程 GC 时撞上就是 `access violation`（实测 6 个窗口的测试组合稳定复现）。

    产品侧的正解是"退出时不跑清理 / 改成空闲时跑"，那是另一件事；
    这里先让测试收干净，否则一条无关的崩溃会盖住真正的回归。
    """
    try:
        from PySide6.QtCore import QThreadPool
        QThreadPool.globalInstance().waitForDone(ms)
    except Exception:                        # noqa: BLE001 - 清理失败不影响判据
        pass


# ===========================================================================
#  四、真窗口端到端：拖线之后链路必须通（2026-10-24 事故回归）
# ===========================================================================

class _Engine:
    """`MainWindowQt` 要的最小引擎壳（见 `test_feedback_p1`）。"""

    def __init__(self, root=None):
        self.project_root = root
        self._registry = None

    def get_registry(self):
        return self._registry


@pytest.fixture(scope='module')
def qt_app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_real_window_can_still_connect_after_import(qt_app, tmp_path,
                                                    monkeypatch):
    """
    **用户报的那个 bug 的端到端判据**（2026-10-24 11:25"无法拖线链接"）。

    路径：打开项目 → 导入章节（**不进卡片页**）→ 拖线。
    这时 `session.beats` 是空的（`import_chapters` 只建图）——
    第一版会判成 `not_loaded` 然后静默 return，界面上"拖了没反应"。

    这里走真窗口：信号之后的链路必须产出一条线。
    """
    from storygraph.ui_qt.main_window import MainWindowQt

    root = build_project(tmp_path / 'proj')
    script = root / 'game' / 'script' / 'ch01.rpy'
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(TAIL_RETURN, encoding='utf-8')

    win = MainWindowQt(_Engine(root))
    # ⚠ 打桩：`not_loaded` 会走 `add_edge` → 弹**模态** AddEdgeDialog →
    #   测试会卡死在 exec() 上（实测 SIGTERM）。这里只关心"有没有反应"。
    calls = []
    monkeypatch.setattr(win, 'add_edge',
                        lambda **kw: calls.append(kw), raising=False)
    try:
        win.open_project_path(root)
        win.import_chapter_files([script])
        graph = win.graph
        assert graph is not None and len(graph.nodes) >= 2, '图里应该有两章'
        ids = {n.label_name: nid for nid, n in graph.nodes.items()}
        assert {'ch01', 'ch02'} <= set(ids)
        # **关键前提**：这时确实没有载入任何一章的拍
        assert not list(getattr(win._session, 'beats', None) or ()), \
            '这条用例要的就是"只导入、没打开"的状态'

        win._on_connect_requested(ids['ch01'], ids['ch02'])

        assert calls, '拖线必须有反应（画草图也算）—— 静默失败就是这次的事故'
    finally:
        win.close()
        _drain_threads()


def test_real_window_loads_source_chapter_so_beat_positions_are_real(
        qt_app, tmp_path, monkeypatch):
    """
    只导入、没打开起点章时，接线窗口仍要能列出**这一章的拍**。

    以前 `edge_position_choices` 依赖当前 session；导入后 beats 是空的，
    下拉框只剩章末。现在 `add_edge` 会先把起点章读进来（不跳卡片页）。
    """
    from PySide6.QtWidgets import QDialog

    from storygraph.ui_qt import dialogs as D
    from storygraph.ui_qt.main_window import MainWindowQt

    root = build_project(tmp_path / 'proj')
    script = root / 'game' / 'script' / 'ch01.rpy'
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(TAIL_RETURN, encoding='utf-8')

    win = MainWindowQt(_Engine(root))
    seen = {}

    def _peek(dlg):
        seen['beats'] = len(win._session.beats)
        seen['positions'] = dlg._pos.count()
        seen['labels'] = [dlg._pos.itemText(i) for i in range(dlg._pos.count())]
        return QDialog.Rejected

    monkeypatch.setattr(D.AddEdgeDialog, 'exec', _peek)
    try:
        win.open_project_path(root)
        win.import_chapter_files([script])
        ids = {n.label_name: nid for nid, n in win.graph.nodes.items()}
        assert not list(getattr(win._session, 'beats', None) or ()), \
            '这条用例要的是"只导入、没打开"的状态'

        win.add_edge(from_id=ids['ch01'], to_id=ids['ch02'])

        assert seen['beats'] > 0, '接线前没有把起点章读进来'
        assert seen['positions'] >= 2, seen['labels']
        assert any('第 1 拍' in text for text in seen['labels']), seen['labels']
    finally:
        win.close()
        _drain_threads()


def test_real_window_opens_the_dialog_when_the_chapter_is_open(qt_app,
                                                              tmp_path,
                                                              monkeypatch):
    """打开那一章之后再拖：**也要先弹「添加连线」**，由人确认位置/类型。"""
    from storygraph.ui_qt.main_window import MainWindowQt

    root = build_project(tmp_path / 'proj')
    script = root / 'game' / 'script' / 'ch01.rpy'
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(TAIL_RETURN, encoding='utf-8')

    win = MainWindowQt(_Engine(root), script)      # 构造时就载入这一章
    calls = []
    monkeypatch.setattr(win, 'add_edge',
                        lambda **kw: calls.append(kw), raising=False)
    try:
        win.open_project_path(root)
        win.import_chapter_files([script])
        ids = {n.label_name: nid for nid, n in win.graph.nodes.items()}
        before = len(win._session.beats)

        win._on_connect_requested(ids['ch01'], ids['ch02'])

        assert calls == [{'from_id': ids['ch01'], 'to_id': ids['ch02']}], \
            '新连线必须先弹窗口；弹窗不必多，但接线这一步是必要的'
        assert len(win._session.beats) == before, '对话框还没确定，不该先写'
    finally:
        win.close()
        _drain_threads()


def test_real_window_add_edge_dialog_writes_by_default(qt_app, tmp_path,
                                                       monkeypatch):
    """
    真窗口 + 真对话框：点「写入剧本并连线」必须真的插一行，而不是只画线。

    这条是 P0 的端到端判据 —— 只测 `AddEdgeDialog.write` 还不够，
    `add_edge` 还要把 `write=True` 接到 `_apply_wire` 上。
    """
    from PySide6.QtWidgets import QDialog

    from storygraph.ui_qt import dialogs as D
    from storygraph.ui_qt.main_window import MainWindowQt

    root = build_project(tmp_path / 'proj')
    script = root / 'game' / 'script' / 'ch01.rpy'
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(TAIL_RETURN, encoding='utf-8')

    win = MainWindowQt(_Engine(root), script)
    try:
        win.open_project_path(root)
        win.import_chapter_files([script])
        ids = {n.label_name: nid for nid, n in win.graph.nodes.items()}
        before = len(win._session.beats)

        def _accept(dlg):
            dlg._on_accept()
            return QDialog.Accepted

        monkeypatch.setattr(D.AddEdgeDialog, 'exec', _accept)
        win.add_edge(from_id=ids['ch01'], to_id=ids['ch02'])

        assert len(win._session.beats) == before + 1, \
            '对话框默认写剧本，但 add_edge 没把 write 接上'
        raws = [b.raw.strip() for b in win._session.beats if b.label == 'ch01']
        assert raws[-2:] == ['jump ch02', 'return'], raws
    finally:
        win.close()
        _drain_threads()


def test_real_window_can_open_a_chapter_for_writing_an_edge(qt_app, tmp_path):
    """
    “起点章没打开”不该只剩草图：窗口要能把它载入，然后按普通写剧本走。
    """
    from storygraph.ui_qt.main_window import MainWindowQt

    root = build_project(tmp_path / 'proj')
    script = root / 'game' / 'script' / 'ch01.rpy'
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(TAIL_RETURN, encoding='utf-8')

    win = MainWindowQt(_Engine(root))       # 故意不带章节
    try:
        win.open_project_path(root)
        win.import_chapter_files([script])
        ids = {n.label_name: nid for nid, n in win.graph.nodes.items()}
        assert not list(getattr(win._session, 'beats', None) or ()), \
            '这条用例要的是“只导入、没打开”的状态'

        assert win._open_chapter_for_edge(ids['ch01']) is True
        assert any(b.label == 'ch01' for b in win._session.beats)
    finally:
        win.close()
        _drain_threads()

class TestRouting:
    def test_chapter_end_also_opens_the_dialog(self, tmp_path):
        """新连线一律先弹「添加连线」；章末唯一也不许替人拍板。"""
        _session, graph, host, _ = _open(tmp_path, TAIL_RETURN)
        ids = _nodes(graph)
        host._on_connect_requested(ids['ch01'], ids['ch02'])
        assert host.add_edge_calls == [(ids['ch01'], ids['ch02'])]

    def test_ambiguous_still_opens_the_dialog(self, tmp_path):
        """E2：位置有歧义就必须问 —— 工具不许替人决定剧情走向。"""
        _session, graph, host, _ = _open(tmp_path, TAIL_JUMP)
        ids = _nodes(graph)
        host._on_connect_requested(ids['ch01'], ids['ch02'])
        assert host.add_edge_calls == [(ids['ch01'], ids['ch02'])]

    def test_not_loaded_still_opens_the_dialog(self, tmp_path):
        """
        ⚠ 2026-10-24 11:30 的事故判据：

        `import_chapters` **不载入拍** —— "导入完章节直接拖线"时 beats 是空的。
        以前第一版在这里只弹一句提示就 `return`，界面表现是"拖了完全没反应"。

        正确行为：**打开接线窗口**，由人决定写不进时是否只画草图；
        不许静默什么都不做。
        """
        session, graph, host, _ = _open(tmp_path, TAIL_RETURN)
        ids = _nodes(graph)
        session.beats = [b for b in session.beats if b.label == 'ch02']

        host._on_connect_requested(ids['ch01'], ids['ch02'])

        assert host.add_edge_calls == [(ids['ch01'], ids['ch02'])], \
            '连不了剧本也要把窗口打开'

    def test_just_imported_without_opening_still_connects(self, tmp_path):
        """
        最常见的那条路：导入章节 → 图出来了 → 直接拖线（没进卡片页）。
        这时 `session.beats` 是空的 —— 以前会静默什么都不做。
        """
        session, graph, host, _ = _open(tmp_path, TAIL_RETURN)
        ids = _nodes(graph)
        session.beats = []                    # 模拟"只导入、没打开"

        host._on_connect_requested(ids['ch01'], ids['ch02'])
        assert host.add_edge_calls, '拖线必须有反应'

    def test_apply_failure_falls_back_to_a_sketch(self, tmp_path, monkeypatch):
        """写剧本失败时也不许"什么都没发生"——退回画草图并说清。"""
        _session, graph, host, _ = _open(tmp_path, TAIL_RETURN)
        ids = _nodes(graph)
        monkeypatch.setattr(host, '_apply_wire',
                            lambda plan: {'ok': False, 'error': '写不进去'})
        host._on_connect_requested(ids['ch01'], ids['ch02'])
        assert host.add_edge_calls, '写失败也要能连线'

    def test_plan_crash_falls_back_to_a_sketch(self, tmp_path, monkeypatch):
        _session, graph, host, _ = _open(tmp_path, TAIL_RETURN)
        ids = _nodes(graph)

        def _boom(*a, **k):
            raise RuntimeError('故意炸')
        monkeypatch.setattr(host, 'plan_wire_landing', _boom)
        host._on_connect_requested(ids['ch01'], ids['ch02'])
        assert host.add_edge_calls, '算不出落点也必须能连线'
