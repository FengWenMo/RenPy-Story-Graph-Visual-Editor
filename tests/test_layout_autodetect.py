# -*- coding: utf-8 -*-
"""
版式**自动认领**：项目里别处存的布局也能自己找来（2026-11-05）
============================================================

用户真机报的：

> 「为什么我明明导入了项目，却没有成功导入布局……？你能自动识别到对应布局的
>   json 文件，并自动导入吗？（不能就算，没有也静默，不用报错）」

真机事实（`D:\\千连万理\\sr_general`，实测）：

| 文件 | 时间 | 形状 | 43 块坐标 |
|---|---|---|---|
| `.storygraph/layout.json` | 10-26 22:26 | 新版瘦格式（无编排线） | 竖着一条线 |
| `game/chapter/目前保存的布局(自动生成的，不用看）.json` | 10-28 00:29 | 老版整图（**50 条编排线**） | 四列网格 |

工具从前只认第一份 → 打开项目看到的是**两天前**摆的样子，而且那 50 条
编排线一条都没回来。这一份盯的就是"认领"这条新路：

- **能对上多少块**优先（对不上的版式再新也没用）；
- 一样多时取**文件更新**的那份（"我最后存的那一版"）；
- 挑不到 / 坏文件 / 没有版式 → **静默**（用户明确说了不用报错）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from storygraph.app import layout_store as LS               # noqa: E402

#: 真机项目（有就跑一条端到端，没有就跳过 —— CI 上不该依赖它）
REAL = Path(r'D:\千连万理\sr_general')


def _node(label: str, rel: str, x: float = 0, y: float = 0):
    """`node_key` 只需要这三样；用 SimpleNamespace 免得拖进整张图。"""
    return SimpleNamespace(label_name=label, file_path=str(rel), x=x, y=y)


def _graph(nodes):
    return SimpleNamespace(nodes={str(i): n for i, n in enumerate(nodes)})


def _new_layout(path: Path, coords: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        'version': 2,
        'nodes': {k: {'x': x, 'y': y} for k, (x, y) in coords.items()},
        'edges': [],
    }, ensure_ascii=False), encoding='utf-8')


def _legacy_layout(path: Path, rows, edges=()):
    """老版整图：`nodes` 是 id → {file_path, label, x, y, statements…}。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    nodes = {}
    for i, (rel, label, x, y) in enumerate(rows):
        nodes[str(i + 1)] = {
            'file_path': str(rel), 'label': label, 'x': x, 'y': y,
            'operations': [], 'statements': ['一行会过期的内容'],
        }
    path.write_text(json.dumps({
        'nodes': nodes,
        'edges': {str(i): e for i, e in enumerate(edges)},
        'next_ids': {},
    }, ensure_ascii=False), encoding='utf-8')


def _touch_newer(path: Path, seconds: float = 60.0):
    """把这份文件的 mtime 往后推（`scan_candidates` 按 mtime 新到旧）。"""
    import os
    st = path.stat()
    os.utime(path, (st.st_atime + seconds, st.st_mtime + seconds))


@pytest.fixture
def project(tmp_path):
    root = tmp_path / 'proj'
    (root / 'game' / 'chapter').mkdir(parents=True)
    return root


class TestPick:
    def test_prefers_the_newer_one_when_both_match(self, project):
        """真机那一对：手动存的那份更新、也更能对上 → 必须是它。"""
        graph = _graph([
            _node('ch01', project / 'game' / 'chapter' / 'ch01.rpy'),
            _node('ch02', project / 'game' / 'chapter' / 'ch02.rpy'),
        ])
        _new_layout(project / '.storygraph' / 'layout.json', {
            'game/chapter/ch01.rpy::ch01': (0, 0),
            'game/chapter/ch02.rpy::ch02': (0, 134),
        })
        manual = project / 'game' / 'chapter' / '我保存的布局.json'
        _legacy_layout(manual, [
            (project / 'game' / 'chapter' / 'ch01.rpy', 'ch01', 10, 20),
            (project / 'game' / 'chapter' / 'ch02.rpy', 'ch02', 300, 20),
        ])
        _touch_newer(manual)

        got = LS.best_for(graph, project)
        assert got['ok'] is True, got
        assert got['path'] == manual, got['path']
        assert got['matched'] == 2
        assert got['data']['nodes']['game/chapter/ch01.rpy::ch01'] == {
            'x': 10.0, 'y': 20.0}

    def test_newer_file_with_no_match_is_skipped(self, project):
        """新，但一块都对不上（上一章 / 别的项目的残留）→ 不许拿它打散图。"""
        graph = _graph([_node('ch01',
                              project / 'game' / 'chapter' / 'ch01.rpy')])
        canon = project / '.storygraph' / 'layout.json'
        _new_layout(canon, {'game/chapter/ch01.rpy::ch01': (5, 6)})
        stray = project / 'game' / 'stray.json'
        _legacy_layout(stray, [('D:/别的项目/game/x.rpy', 'X', 999, 999)])
        _touch_newer(stray, 600)

        got = LS.best_for(graph, project)
        assert got['path'] == canon, got['path']
        assert got['matched'] == 1

    def test_more_matched_beats_more_recent(self, project):
        """只覆盖 1 块的新文件，不许盖掉覆盖 2 块的旧文件。"""
        graph = _graph([
            _node('ch01', project / 'game' / 'chapter' / 'ch01.rpy'),
            _node('ch02', project / 'game' / 'chapter' / 'ch02.rpy'),
        ])
        canon = project / '.storygraph' / 'layout.json'
        _new_layout(canon, {
            'game/chapter/ch01.rpy::ch01': (1, 1),
            'game/chapter/ch02.rpy::ch02': (2, 2),
        })
        partial = project / 'game' / 'chapter' / '只有一块.json'
        _legacy_layout(partial, [
            (project / 'game' / 'chapter' / 'ch01.rpy', 'ch01', 9, 9)])
        _touch_newer(partial, 600)

        got = LS.best_for(graph, project)
        assert got['path'] == canon, got['path']
        assert got['matched'] == 2

    def test_broken_json_is_skipped_and_does_not_raise(self, project):
        graph = _graph([_node('ch01',
                              project / 'game' / 'chapter' / 'ch01.rpy')])
        bad = project / 'game' / 'chapter' / '坏了.json'
        bad.write_text('{"nodes": { 这不是 JSON', encoding='utf-8')
        _touch_newer(bad, 600)
        canon = project / '.storygraph' / 'layout.json'
        _new_layout(canon, {'game/chapter/ch01.rpy::ch01': (1, 1)})

        got = LS.best_for(graph, project)
        assert got['path'] == canon, got['path']

    def test_non_layout_json_is_ignored(self, project):
        """项目里别的 JSON（资源表导出、配置）不许被当版式。"""
        graph = _graph([_node('ch01',
                              project / 'game' / 'chapter' / 'ch01.rpy')])
        (project / 'game' / 'config.json').write_text(
            json.dumps({'nodes': ['a', 'b']}), encoding='utf-8')
        assert LS.best_for(graph, project)['ok'] is False

    def test_name_does_not_matter_only_shape(self, project):
        """**名字里没有任何版式字样**的 JSON，只要形状对就得认（2026-11-05）。

        用户原话："下次别通过名字认 json，看结构！" —— 真机那份叫
        「目前保存的布局(自动生成的，不用看）.json」，靠名字猜根本猜不到；
        反过来叫 `layout.json` 的也可能是随包预设（见真机回归）。
        """
        graph = _graph([_node('ch01',
                              project / 'game' / 'chapter' / 'ch01.rpy')])
        odd = project / 'game' / 'chapter' / 'abc_2026-10-28(1).json'
        _new_layout(odd, {'game/chapter/ch01.rpy::ch01': (7, 8)})
        got = LS.best_for(graph, project)
        assert got['ok'] is True, got
        assert got['path'] == odd

    def test_nodes_list_is_not_a_layout_even_if_named_layout(self, project):
        """反过来：名字叫 `layout.json`、`nodes` 是列表 → **不是**版式。"""
        graph = _graph([_node('ch01',
                              project / 'game' / 'chapter' / 'ch01.rpy')])
        fake = project / '.storygraph' / 'layout.json'
        fake.parent.mkdir(parents=True)
        fake.write_text(json.dumps({'nodes': [{'x': 1}]}), encoding='utf-8')
        assert LS.scan_candidates(project) == []
        assert LS.best_for(graph, project)['ok'] is False

    def test_nodes_key_far_into_the_file_is_found(self, project):
        """老格式版式把 `operations` 放在前面：`nodes` 在 100 KB 之后也要找到。

        从前只看头 8 KB —— 真机那份 134 KB 的版式一旦 `nodes` 排在后面就
        整个漏掉（"明明有布局却导不进来"的另一半原因）。
        """
        graph = _graph([_node('ch01',
                              project / 'game' / 'chapter' / 'ch01.rpy')])
        big = project / 'game' / 'chapter' / 'big.json'
        filler = {'operations': [('这是一段会过期的内容 ' * 20)] * 900}
        nodes = {'1': {'file_path': str(project / 'game' / 'chapter'
                                        / 'ch01.rpy'),
                       'label': 'ch01', 'x': 3, 'y': 4}}
        big.write_text(json.dumps({'extra': filler, 'nodes': nodes},
                                  ensure_ascii=False), encoding='utf-8')
        assert big.stat().st_size > 100 * 1024
        got = LS.best_for(graph, project)
        assert got['ok'] is True, got
        assert got['path'] == big

    def test_empty_graph_is_silent(self, project):
        _new_layout(project / '.storygraph' / 'layout.json',
                    {'game/chapter/ch01.rpy::ch01': (1, 1)})
        got = LS.best_for(_graph([]), project)
        assert got['ok'] is False and got['reason']

    def test_project_without_any_layout_is_silent(self, project):
        got = LS.best_for(_graph([_node('ch01', 'game/chapter/ch01.rpy')]),
                          project)
        assert got['ok'] is False
        assert got['path'] is None
        assert got['reason'], '失败要有原因（调用方自己决定说不说）'

    def test_scan_respects_depth_and_skips_heavy_dirs(self, project):
        """`images/` 里藏的 json 不扫（性能护栏）；深过 4 层的也不扫。"""
        deep = project / 'game' / 'a' / 'b' / 'c' / 'd' / 'e'
        deep.mkdir(parents=True)
        _new_layout(deep / '太深.json', {'x::y': (0, 0)})
        _new_layout(project / 'game' / 'images' / '假的.json',
                    {'x::y': (0, 0)})
        assert LS.scan_candidates(project) == []


class TestApply:
    def test_the_picked_one_actually_moves_the_nodes(self, project):
        """挑出来还不够 —— 贴上去要真的改坐标、真的补编排线。"""
        n1 = _node('ch01', project / 'game' / 'chapter' / 'ch01.rpy')
        n2 = _node('ch02', project / 'game' / 'chapter' / 'ch02.rpy')
        graph = _graph([n1, n2])
        graph.nodes['0'].id = 0
        graph.nodes['1'].id = 1
        manual = project / 'game' / 'chapter' / '布局.json'
        _legacy_layout(
            manual,
            [(project / 'game' / 'chapter' / 'ch01.rpy', 'ch01', 10, 20),
             (project / 'game' / 'chapter' / 'ch02.rpy', 'ch02', 300, 20)],
            edges=[{'from': '1', 'to': '2', 'type': 'jump',
                    'origin': 'control', 'position': 'control_end'}],
        )

        picked = LS.best_for(graph, project)
        added = []
        graph.add_edge = lambda *a, **k: added.append((a, k))
        res = LS.apply_placement(graph, picked['data'], project)
        assert (n1.x, n1.y) == (10.0, 20.0)
        assert (n2.x, n2.y) == (300.0, 20.0)
        assert res['edges_added'] == 1, '老文件里的编排线必须回来'
        assert added and added[0][1].get('origin') == 'control'


@pytest.mark.skipif(not (REAL / '.storygraph' / 'layout.json').is_file(),
                    reason='真机项目不在这台机器上')
def test_real_project_picks_the_manual_save():
    """真机回归：两份都在时，必须挑 `game/chapter/` 那份（更新、带编排线）。"""
    canon = LS.read_placement(REAL / '.storygraph' / 'layout.json', REAL)
    nodes = []
    for key in canon['nodes']:
        rel, label = key.rsplit('::', 1)
        nodes.append(_node(label, REAL / rel))
    graph = _graph(nodes)

    got = LS.best_for(graph, REAL)
    assert got['ok'] is True, got
    assert got['path'].name != 'layout.json', got['path']
    assert got['matched'] == len(nodes)
