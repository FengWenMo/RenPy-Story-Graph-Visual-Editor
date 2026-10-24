# -*- coding: utf-8 -*-
"""
L7 完整版：打开项目时**把上次的剧情图重建出来**，再贴版式。

为什么这一步非有不可：
    W1b 只在"导入章节之后"贴版式。可**重开软件时图是空的** ——
    没有节点可贴，版式照样用不上，用户看到的还是"上次摆好的又没了"。

判据分三层：
  1. 记忆本身（相对路径、丢文件、空表）；
  2. 重建出来的图与上次**节点一致**；
  3. 版式贴得上（坐标回到原位）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ui_project import build_project  # noqa: E402

from storygraph.app.editor import EditorSession  # noqa: E402
from storygraph.core import session_store as SS  # noqa: E402
from storygraph.graph.models import Graph  # noqa: E402
from storygraph.ui_qt.window_graph_ops import GraphOpsMixin  # noqa: E402


@pytest.fixture
def store(tmp_path, monkeypatch):
    """把会话记忆指向临时文件，并临时打开写开关（conftest 默认是关的）。"""
    monkeypatch.setenv('STORYGRAPH_SESSION_CONFIG', str(tmp_path / 'session.json'))
    SS.set_enabled(True)
    yield tmp_path / 'session.json'
    SS.set_enabled(False)


@pytest.fixture
def project(tmp_path):
    root = build_project(tmp_path / 'proj')
    for name in ('ch01', 'ch02'):
        p = root / 'game' / 'script' / f'{name}.rpy'
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f'label {name}:\n    e "{name}"\n    return\n',
                     encoding='utf-8')
    return root


def _import(root, names):
    session = EditorSession(project_root=root)
    graph = Graph()
    for name in names:
        res = session.import_chapters(
            [root / 'game' / 'script' / f'{name}.rpy'], graph)
        assert res['ok'], res.get('error')
        graph = res['graph']
    return session, graph


# ===========================================================================
#  一、记忆
# ===========================================================================

class TestMemory:
    def test_stores_relative_paths(self, store, project):
        _session, graph = _import(project, ['ch01', 'ch02'])
        files = _session.graph_files(graph)
        assert len(files) == 2

        SS.remember_graph_files(project, files)

        raw = SS.project_layout(project).get('graph_files')
        assert raw == ['game/script/ch01.rpy', 'game/script/ch02.rpy'], raw

    def test_reads_back_absolute_paths(self, store, project):
        _session, graph = _import(project, ['ch01'])
        SS.remember_graph_files(project, _session.graph_files(graph))

        got = SS.graph_files(project)
        assert len(got) == 1 and Path(got[0]).is_absolute()
        assert Path(got[0]).name == 'ch01.rpy'

    def test_deleted_files_are_skipped(self, store, project):
        _session, graph = _import(project, ['ch01', 'ch02'])
        SS.remember_graph_files(project, _session.graph_files(graph))

        (project / 'game' / 'script' / 'ch02.rpy').unlink()
        got = SS.graph_files(project)
        assert [Path(x).name for x in got] == ['ch01.rpy'], '删掉的不该硬塞回来'

    def test_empty_list_is_remembered_as_empty(self, store, project):
        """
        空表和"从没记过"必须区分：前者是"这个项目没有图"，
        后者是"我还没见过这个项目"。混为一谈会让自动恢复去乱猜。
        """
        SS.remember_graph_files(project, [])
        assert SS.project_layout(project).get('graph_files') == []
        assert SS.graph_files(project) == []

    def test_duplicates_collapse(self, store, project):
        p = project / 'game' / 'script' / 'ch01.rpy'
        SS.remember_graph_files(project, [p, p, str(p)])
        assert SS.project_layout(project).get('graph_files') == \
            ['game/script/ch01.rpy']


# ===========================================================================
#  二、重建出来的图要和上次一样
# ===========================================================================

def test_round_trip_rebuilds_the_same_nodes(store, project):
    s1, g1 = _import(project, ['ch01', 'ch02'])
    SS.remember_graph_files(project, s1.graph_files(g1))
    labels_before = sorted(n.label_name for n in g1.nodes.values())

    # 模拟"重开软件"：全新会话，图是空的
    s2 = EditorSession(project_root=project)
    files = SS.graph_files(project)
    assert files, '重新打开时应该能读回上次那批文件'
    res = s2.import_chapters(files, Graph())

    assert res['ok'], res.get('error')
    labels_after = sorted(n.label_name for n in res['graph'].nodes.values())
    assert labels_after == labels_before


def test_placement_is_applied_on_top_of_the_rebuilt_graph(store, project):
    """
    重建之后再贴坐标 —— 这才是"摆好的版式回来了"的完整链路。
    """
    from storygraph.app.layout_store import apply_placement, placement_dict

    _s1, g1 = _import(project, ['ch01', 'ch02'])
    SS.remember_graph_files(project, _s1.graph_files(g1))
    # 用户把 ch01 拖到 (321, 123)
    node = next(n for n in g1.nodes.values() if n.label_name == 'ch01')
    node.x, node.y = 321, 123
    saved = placement_dict(g1, project)

    # 重开：重建图 → 贴版式
    _s2, g2 = _import(project, ['ch01', 'ch02'])
    moved = next(n for n in g2.nodes.values() if n.label_name == 'ch01')
    assert (moved.x, moved.y) != (321, 123), '重建出来的图应该是自动排版的位置'

    res = apply_placement(g2, {'ok': True, **saved}, project)
    assert res['matched'] == 2
    assert (moved.x, moved.y) == (321, 123), '版式没贴回去'


def test_missing_everything_is_harmless(store, project):
    """从没记过 → 读回空表，不抛。"""
    assert SS.graph_files(project) == []


# ===========================================================================
#  三、恢复入口本身：三个前提缺一不做
# ===========================================================================

class _Host:
    """够 `_restore_graph_files` 跑完的最小上下文。"""

    def __init__(self, root, graph=None):
        self._project_root = root
        self.graph = graph
        self.imported = None
        self.logs = []

    def _log(self, msg):
        self.logs.append(str(msg))

    def import_chapter_files(self, files):
        self.imported = list(files)
        return {'ok': True}


_Host._restore_graph_files = GraphOpsMixin._restore_graph_files


def _seed(project, names=('ch01', 'ch02')):
    s, g = _import(project, list(names))
    SS.remember_graph_files(project, s.graph_files(g))
    return s, g


def test_restores_when_the_graph_is_empty(store, project):
    _seed(project)
    host = _Host(project, graph=Graph())     # 空图
    assert host._restore_graph_files() == 2
    assert host.imported and len(host.imported) == 2


def test_does_not_touch_a_graph_that_already_has_nodes(store, project):
    _seed(project)
    _s, g = _import(project, ['ch01'])
    host = _Host(project, graph=g)
    assert host._restore_graph_files() == 0
    assert host.imported is None, '用户手上已有工作时不许自动重置'


def test_respects_the_auto_open_switch(store, project):
    _seed(project)
    SS.set_auto_open(False)
    host = _Host(project, graph=Graph())
    assert host._restore_graph_files() == 0
    assert host.imported is None, '关掉记忆的人就是不想要这个'
    SS.set_auto_open(True)
