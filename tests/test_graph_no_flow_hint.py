# -*- coding: utf-8 -*-
"""
2026-10-27 真机："导入了 7 个章节，图上一根线都没有"。

原因不是 bug（那 7 个 `.rpy` 里除了 `return` 没有 `jump` / `call`），但界面
只报了一句"连线 0 条" —— 和 2026-10-03 那条"我连线呢？"是同一类：
**没有线的图必须说清为什么没有、以及怎么才有**。

这里钉两件事：

  · 整张图 0 条线、章节之间也没有 jump/call、项目**有**编排层 →
    控制台必须点名那个文件（`chapter_controller.rpy`）并给出两条路；
  · 图上有线 / 只有 1 个块 / 编排层已经导入过 → 不许说错话。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

sys.path.insert(0, str(Path(__file__).resolve().parent))

from storygraph.graph.models import Graph  # noqa: E402
from storygraph.ui_qt.window_graph_ops import GraphOpsMixin  # noqa: E402

CONTROLLER = ('label chapter1_4_control:\n    call chapter1_4\n'
              '    jump chapter2_1_control\n\n'
              'label chapter2_1_control:\n    call chapter2_1\n    return\n')


class _Session:
    def __init__(self, files=()):
        self._files = [str(p) for p in files]

    def graph_files(self, graph):
        return list(self._files)


class _Win:
    _report_no_flow = GraphOpsMixin._report_no_flow

    def __init__(self, root, graph, files=()):
        self._project_root = root
        self._session = _Session(files)
        self._log_rows = []

    def _log(self, msg):
        self._log_rows.append(str(msg))

    def text(self):
        return '\n'.join(self._log_rows)


def _project(tmp_path, with_controller=True):
    """4 个只有 `label` + `return` 的章节（照真机的形状）。"""
    root = tmp_path / 'proj'
    script = root / 'game' / 'chapter'
    script.mkdir(parents=True)
    for name in ('chapter1_4', 'chapter2_1', 'chapter2_2_3', 'chapter2_3'):
        (script / f'{name}.rpy').write_text(
            f'label {name}:\n    e "台词"\n    return\n', encoding='utf-8')
    if with_controller:
        (script / 'chapter_controller.rpy').write_text(CONTROLLER,
                                                       encoding='utf-8')
    return root, script


def _graph_of(labels):
    g = Graph()
    for name in labels:
        g.add_node(name, 0, 0)
    return g


def _run(root, graph, files=()):
    win = _Win(root, graph, files=files)
    win._log_rows = []
    win._report_no_flow(graph)
    return win


def test_says_why_there_is_no_line_and_points_at_the_control_file(tmp_path):
    root, _script = _project(tmp_path)
    graph = _graph_of(['chapter1_4', 'chapter2_1', 'chapter2_2_3', 'chapter2_3'])
    text = _run(root, graph).text()
    assert '一条连线都没有' in text
    assert 'chapter_controller.rpy' in text          # 点名编排层
    assert '补全缺失章节' in text                     # 一键入口
    assert 'Shift + 左键' in text                     # 自己接线的手势


def test_stays_quiet_when_the_graph_already_has_lines(tmp_path):
    root, _script = _project(tmp_path)
    graph = _graph_of(['chapter1_4', 'chapter2_1'])
    graph.add_edge(1, 2)
    assert _run(root, graph).text() == ''


def test_stays_quiet_on_a_single_node(tmp_path):
    root, _script = _project(tmp_path)
    graph = _graph_of(['chapter1_4'])
    assert _run(root, graph).text() == ''


def test_control_file_already_imported_is_not_suggested_again(tmp_path):
    root, script = _project(tmp_path)
    graph = _graph_of(['chapter1_4', 'chapter2_1'])
    text = _run(root, graph,
                files=[script / 'chapter_controller.rpy']).text()
    assert '已经导进来了' in text
    assert '把它也导入' not in text


def test_project_without_a_control_file_does_not_invent_one(tmp_path):
    root, _script = _project(tmp_path, with_controller=False)
    graph = _graph_of(['chapter1_4', 'chapter2_1'])
    text = _run(root, graph).text()
    assert '一条连线都没有' in text
    assert 'chapter_controller' not in text
    assert 'Shift + 左键' in text                     # 没有编排层也还有出路
