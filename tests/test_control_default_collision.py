# -*- coding: utf-8 -*-
"""
控制流产物**不许重复声明**项目里已有的变量（2026-10-28，真机 lint 抓到的）。

现场（修好 `Plotnode` → `Plotnodes` 之后第一次 lint）：

    Exception: store.Plotnodes211 is being given a default a second time.
    File "game/plottree.rpy", line 7, in execute_default
        default Plotnodes211 = 0

`plottree.rpy` 是作者声明剧情变量的地方（`default Plotnodes211 = 0`），
控制流产物又写了一遍 → **游戏起不来**，lint 直接 exit 1。

巧合的是：这个问题一直被"变量名拼错"掩盖着（两边名字不同，谁也不撞谁）。
名字一改对，它就立刻现形 —— 所以两条得一起修。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ui_project import build_project  # noqa: E402

from storygraph.graph.models import Graph  # noqa: E402
from storygraph.pipeline.renpy_exporter import render_control_text  # noqa: E402


def _graph_writing(*codes, label='ch01'):
    g = Graph()
    nid = g.add_node(label, 0, 0)
    g.nodes[nid].set_operations(list(codes))
    return g


@pytest.fixture  # noqa: F821 - pytest 由 conftest 提供
def project(tmp_path):
    return build_project(tmp_path / 'proj')


def test_variable_declared_elsewhere_is_not_declared_again(project):
    (project / 'game' / 'script' / 'plottree.rpy').write_text(
        'default Plotnodes211 = 0\ndefault love = 0\n', encoding='utf-8')
    out = project / 'game' / 'script' / 'controller.rpy'

    text = render_control_text(_graph_writing('Plotnodes211=1', 'love += 1'),
                               wrapper=True, out_path=out)

    assert 'default Plotnodes211' not in text
    assert 'default love' not in text
    assert '$ Plotnodes211=1' in text, '变量不声明，赋值照写'


def test_variable_only_the_graph_knows_is_still_declared(project):
    out = project / 'game' / 'script' / 'controller.rpy'

    text = render_control_text(_graph_writing('flag = 1'), wrapper=True,
                               out_path=out)

    assert 'default flag = 0' in text


def test_no_target_path_keeps_the_old_behaviour(project):
    """没给目标路径（预览/单测）时无法判断项目根 → 照旧声明。"""
    text = render_control_text(_graph_writing('flag = 1'), wrapper=True)
    assert 'default flag = 0' in text
