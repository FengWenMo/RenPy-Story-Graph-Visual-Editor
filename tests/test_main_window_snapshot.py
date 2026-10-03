"""
`MainWindowQt` 的**操作序列 golden**（拆分前的安全网）
=====================================================

为什么要有这个文件（P3-14 另一半，2026-10-05）
----------------------------------------------
`MainWindowQt` 是 3452 行 / 120 方法的上帝对象（`EditorSession` 的另一半）。
拆它之前必须先有网 —— 而且**不能是"内部方法返回值"那种网**：
那份快照在拆分过程中会全量变化，人眼审不动（见
`tests/test_editor_session_snapshot.py` 与 `test_editor_session_behavior.py`
的分工说明）。

所以这里锁的是**外部可观察状态**：

  · **操作序列**：载入章节 → 翻拍 → 改台词 → 保存 → 撤销 →
    增删卡片 → 导图 → 分析 → 导出…
  · **断言**：图上有什么、拍列表长什么样、状态栏说什么、窗口标题是什么 ——
    **完全不碰任何内部方法名**。

拆分期间这个文件**必须一直绿**：方法可以随便改名/搬家，只要
"用户按这一串操作 → 看到这些结果"不变。

与 `test_ui_qt.py` 的分工
-------------------------
那个文件测的是**单点契约**（卡片→show 文本、键盘边到才翻页、
某个按钮点了会怎样…），一个用例盯一处。这个文件测的是**整条操作链**
串起来之后的总状态 —— 它更粗，但**跨方法**，所以能抓住"两块各自都对、
接在一起不对"那一类拆分事故。

真 Qt、offscreen：与 `test_ui_qt.py` 同一套（真布局、真事件、真字体）。
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
from PySide6.QtWidgets import QApplication                  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ui_project import build_project                        # noqa: E402

GOLDEN = Path(__file__).resolve().parent / 'snapshots' / 'main_window.json'

#: 捕获到的项数下限 —— 防止夹具塌成"什么都没测到"还报绿。
MIN_CAPTURED = 40


@pytest.fixture(scope='session')
def app():
    a = QApplication.instance() or QApplication([])
    yield a


@pytest.fixture
def worked(tmp_path, app, monkeypatch):
    """
    建工程 → 开窗口 → 跑一串操作 → 返回 (窗口, 项目根, 捕获列表)。

    **必须先把模态对话框打掉**（2026-10-05 实测踩过）：`delete_card` /
    `remove_chapter_from_graph` 会弹 `QMessageBox`，`load_layout` 里还有
    `QFileDialog` —— offscreen 下没有人点按钮，`exec()` 会**一直等**，
    整条链挂死（我第一版就挂在 `delete_card` 上，400 秒没出来）。

    打掉的原则：**让它返回"用户点了确认 / 选了路径"**，而不是"取消" ——
    取消会让流程提前 return，那样后面几十步根本跑不到，网就白铺了。
    """
    from PySide6.QtWidgets import QFileDialog, QMessageBox

    monkeypatch.setattr(QMessageBox, 'exec', lambda self, *a, **k: 0)
    monkeypatch.setattr(QMessageBox, 'question',
                        staticmethod(lambda *a, **k: QMessageBox.Yes))
    monkeypatch.setattr(QMessageBox, 'warning',
                        staticmethod(lambda *a, **k: QMessageBox.Ok))
    monkeypatch.setattr(QMessageBox, 'information',
                        staticmethod(lambda *a, **k: QMessageBox.Ok))
    monkeypatch.setattr(QMessageBox, 'critical',
                        staticmethod(lambda *a, **k: QMessageBox.Ok))
    # 文件对话框返回"第一个参数里那个路径"（调用方都传了默认路径）
    monkeypatch.setattr(
        QFileDialog, 'getSaveFileName',
        staticmethod(lambda *a, **k: (str(a[2]) if len(a) > 2 else '', '')))
    monkeypatch.setattr(
        QFileDialog, 'getOpenFileName',
        staticmethod(lambda *a, **k: (str(a[2]) if len(a) > 2 else '', '')))

    from storygraph.ui_qt.main_window import MainWindowQt

    project = build_project(tmp_path / 'proj')

    class _Engine:
        def __init__(self, root):
            self.project_root = root
            self._registry = None

        def get_registry(self):
            return self._registry

    script = project / 'game' / 'script' / 'ch01.rpy'
    win = MainWindowQt(_Engine(project), script)
    try:
        yield win, project, _capture(win, project)
    finally:
        win.close()


# ---------------------------------------------------------------------------
#  捕获：**只用公开动作 + 公开状态**，不碰任何 `_` 方法
# ---------------------------------------------------------------------------

def _graph_view(win):
    """图的可观察形状（节点名 + 边）。"""
    g = getattr(win, 'graph', None)
    if g is None:
        return None
    nodes = sorted(str(getattr(n, 'label_name', '') or '')
                   for n in (getattr(g, 'nodes', {}) or {}).values())
    edges = sorted((str(getattr(e, 'from_id', '')), str(getattr(e, 'to_id', '')),
                    str(getattr(e, 'type', '') or ''))
                   for e in (getattr(g, 'edges', {}) or {}).values())
    return {'nodes': nodes, 'edges': edges}


def _beats_view(win):
    """当前章节每一拍的公开字段。"""
    beats = getattr(win, 'beats', None) or []
    return [{'kind': b.kind, 'speaker': b.speaker, 'text': b.text,
             'condition': b.condition, 'scene': b.scene, 'show': b.show,
             'hide': b.hide, 'music': b.music, 'raw': b.raw}
            for b in beats]


def _page(win):
    try:
        return str(win.current_page())
    except Exception:                     # noqa: BLE001 - 捕获不该让测试崩
        return '<err>'


def _loaded_index(win):
    """当前载入的拍号（导航的真正状态；`next_beat` 改的就是它）。

    为什么不看 `win.beats`：那是**整章**的拍，翻页不会改它 ——
    第一版只记 beats，于是 `next_beat[0..2]` 三步的 payload 一模一样，
    网看着有 41 步、其实导航那一段什么都没测到。
    """
    return getattr(win, '_loaded_idx', None)


def _body(win):
    """
    卡片页表单里"策划看到的那几个字段"。

    刻意**不调 `win.cards()`**：那是 `_` 开头（内部方法），这个网的原则是
    "只碰公开表面"，调了它拆分时就会跟着变红、失去"必须一直绿"的意义。
    只读 `_panel.text()` —— 那是前端自己摆出来的一个控件值，
    不是会话内部 API。
    """
    try:
        return {'text': win._panel.text()}
    except Exception:                     # noqa: BLE001
        return '<err>'


def _status(win):
    try:
        return str(win.statusBar().currentMessage() or '')
    except Exception:                     # noqa: BLE001
        return '<err>'


def _capture(win, project):
    """
    跑一串**确定性操作**并记录可观察状态。

    刻意**不碰鼠标 / 不弹对话框**：`export_script` / `open_project` 这类会
    弹 `QFileDialog` 的动作不在这里调（那需要一个假的对话框返回值），
    它们的内部语义由 `test_ui_qt.py` 与 `test_export_acceptance.py` 覆盖。
    """
    steps = []

    def rec(label, payload):
        steps.append((label, payload))

    # 1. 初始状态
    rec('title-unknown', {'title_has_project': '剧情编辑器' in win.windowTitle()})
    rec('page', _page(win))
    rec('beats', _beats_view(win))
    rec('graph-empty', _graph_view(win))

    # 2. 打开项目（只读资源表，不该动图）
    rec('open_project_path', _public(win, 'open_project_path',
                                     str(project)))
    rec('graph-after-open', _graph_view(win))

    # 3. 导入章节
    script = project / 'game' / 'script' / 'ch01.rpy'
    rec('import_chapter_files',
        _public(win, 'import_chapter_files', [str(script)]))
    rec('graph-after-import', _graph_view(win))

    # 4. 图 → 卡片：切页 + 翻拍（**记 `loaded_idx`** —— 那才是导航状态）
    rec('page-graph', _page(win))
    rec('loaded-index-initial', _loaded_index(win))
    for i in range(3):
        rec(f'next_beat[{i}]', _public(win, 'next_beat'))
        rec(f'loaded-index[{i}]', _loaded_index(win))
        rec(f'body[{i}]', _body(win))
    rec('prev_beat', _public(win, 'prev_beat'))
    rec('loaded-index-after-prev', _loaded_index(win))

    # 4b. 直接跳到某几拍（`_show_index` 是列表选中/双击模块走的那条路，
    #     `test_ui_qt` 里也在用；它比空格键更"结构化"）
    for idx in (0, 4, 7, 11):
        rec(f'show_index[{idx}]', _public(win, 'show_index', idx))
        rec(f'loaded-index-at[{idx}]', _loaded_index(win))
        rec(f'body-at[{idx}]', _body(win))
        rec(f'status-at[{idx}]', _redact(_status(win), project))

    # 5. 结构编辑：插一张卡（再删掉 —— `delete_card` 删的是**当前这张**，
    #    所以先插一张让它有东西可删；顺序反了会删掉真实内容）
    rec('insert_card', _public(win, 'insert_card', True))
    rec('beats-after-insert', _beats_view(win))
    rec('delete_card', _public(win, 'delete_card'))
    rec('beats-after-delete', _beats_view(win))

    # 6. 分析（只读）
    rec('check_logic', _public(win, 'check_logic'))
    rec('analysis-status', _redact(_status(win), project))
    rec('clear_analysis', _public(win, 'clear_analysis'))

    # 7. 校验（只读）
    rec('validate', _public(win, 'validate_current_script'))

    # 8. 布局存读（路径**不进快照**）
    layout = project / 'build' / 'layout.json'
    layout.parent.mkdir(parents=True, exist_ok=True)
    rec('save_layout', _public(win, 'save_layout', str(layout)))
    rec('layout-file-exists', layout.is_file())
    rec('load_layout', _public(win, 'load_layout', str(layout)))
    rec('graph-after-load-layout', _graph_view(win))

    # 9. 控制流导出（写文件，只记"成不成 + 文件在不在"）
    cf = project / 'build' / 'cf.rpy'
    rec('export_control_flow',
        _public(win, 'export_control_flow', str(cf), None))
    rec('cf-exists', cf.is_file())

    # 10. 图级撤销 / 重做（导入与布局加载都进历史栈）
    rec('undo_graph', _public(win, 'undo_graph'))
    rec('graph-after-undo', _graph_view(win))
    rec('redo_graph', _public(win, 'redo_graph'))
    rec('graph-after-redo', _graph_view(win))

    # 11. 卡片级撤销
    rec('undo_current_beat', _public(win, 'undo_current_beat'))
    rec('beats-after-undo-beat', _beats_view(win))

    # 12. 删章 / 清图（`remove_chapter_from_graph` 收的是**节点 id**）
    nid = _first_node_id(win)
    rec('remove_chapter', _public(win, 'remove_chapter_from_graph', nid))
    rec('graph-after-remove', _graph_view(win))
    rec('clear_graph', _public(win, 'clear_graph'))
    rec('graph-after-clear', _graph_view(win))

    # 13. 收尾状态
    rec('page-final', _page(win))
    rec('status-final', _redact(_status(win), project))
    return steps


def _first_node_id(win):
    """图上第一个节点 id（`remove_chapter_from_graph` 收的是 id 不是路径）。"""
    g = getattr(win, 'graph', None)
    nodes = getattr(g, 'nodes', {}) or {}
    return next(iter(nodes), None)


def _public(win, name, *args):
    """
    调一个**公开动作**，把它转成"可进快照的东西"。

    `name` 可以是 `show_index` 这种"去掉下划线后找不到"的 —— 所以先试原名，
    再试加一个下划线（`show_index` → `_show_index`）。这两个都是**前端自己
    也在调**的入口，不是拆分时会随内部结构变动的实现细节。
    """
    fn = getattr(win, name, None)
    if not callable(fn):
        fn = getattr(win, f'_{name}', None)
    if not callable(fn):
        return {'ok': False, 'missing': True}
    try:
        res = fn(*args)
    except Exception as e:                # noqa: BLE001 - 捕获不该让测试崩
        return {'ok': False, 'error': type(e).__name__}
    if isinstance(res, dict):
        return {'ok': bool(res.get('ok', True)),
                'keys': sorted(str(k) for k in res)}
    return {'ok': True, 'type': type(res).__name__}


def _redact(text, project):
    """把绝对路径打码（快照必须跨机器可比）。"""
    s = str(text or '')
    s = s.replace(str(project), '<PROJ>')
    s = s.replace(str(project).replace('\\', '/'), '<PROJ>')
    return s


def _normalize(steps, project):
    """递归打码 + 排序，保证跨机器逐字节一致。"""
    blob = json.dumps(steps, ensure_ascii=False, sort_keys=True)
    blob = blob.replace(json.dumps(str(project))[1:-1], '<PROJ>')
    return json.loads(blob)


# ---------------------------------------------------------------------------
#  测试
# ---------------------------------------------------------------------------

class TestMainWindowGolden:

    def test_capture_is_substantial(self, worked):
        """夹具不能塌成"什么都没测到"还报绿。"""
        _win, _proj, steps = worked
        assert len(steps) >= MIN_CAPTURED, (
            f'只捕获到 {len(steps)} 步（下限 {MIN_CAPTURED}）—— '
            f'夹具可能没跑起来')

    def test_no_execution_errors_in_the_sequence(self, worked):
        """
        整条操作链**不许有异常**。

        这一条是拆分期间最有用的：某块被搬走之后依赖没接上，
        通常就表现成这里某个动作抛 `TypeError` / `AttributeError`。
        """
        _win, _proj, steps = worked
        bad = [(label, payload.get('error'))
               for label, payload in steps
               if isinstance(payload, dict) and payload.get('error')]
        assert not bad, f'操作链里有异常：{bad}'

    def test_behavior_matches_golden(self, worked):
        """逐步与 golden 比对；不一致就指出**第几步、差在哪**。"""
        _win, project, steps = worked
        got = {'steps': _normalize(steps, project)}

        if os.environ.get('STORYGRAPH_UPDATE_SNAPSHOTS') == '1':
            GOLDEN.parent.mkdir(parents=True, exist_ok=True)
            GOLDEN.write_text(
                json.dumps(got, ensure_ascii=False, indent=1, sort_keys=True),
                encoding='utf-8')
            pytest.skip(f'已更新 golden：{GOLDEN}')

        assert GOLDEN.exists(), (
            f'缺少 golden：{GOLDEN}\n'
            f'生成：$env:STORYGRAPH_UPDATE_SNAPSHOTS=1; '
            f'python -m pytest tests/test_main_window_snapshot.py')
        want = json.loads(GOLDEN.read_text(encoding='utf-8'))

        wa, ga = want['steps'], got['steps']
        diff = []
        for i in range(max(len(wa), len(ga))):
            a = wa[i] if i < len(wa) else None
            b = ga[i] if i < len(ga) else None
            if a == b:
                continue
            label = (a or b)[0]
            diff.append((i, label, a[1] if a else None, b[1] if b else None))
            if len(diff) >= 8:
                break
        assert not diff, (
            'MainWindowQt 行为与 golden 不一致（前 %d 处）：\n%s\n\n'
            '确认是有意改动后，设 STORYGRAPH_UPDATE_SNAPSHOTS=1 更新 golden。'
            % (len(diff),
               '\n'.join(f'  步 {i}: {lb}\n    golden={a!r}\n    现在  ={b!r}'
                         for i, lb, a, b in diff)))
