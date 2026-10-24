# -*- coding: utf-8 -*-
"""
W1a：版式（layout）只存坐标，不存内容；主键是稳定身份。

这里钉的每条都对应用户真实痛过一次：

  1. 版式文件里**不能有** `operations` / `statements`（那是内容，会过期）
  2. 主键**不能是内部 id**（新增一章后 id 串位，实测 `id1` 从 A 变 Z）
  3. 加载版式**不能整图替换**（新章会消失）
  4. 老格式要**读得回来**（只取坐标），但不自动改写用户文件
"""
from __future__ import annotations

import json

from storygraph.app.layout_store import (
    PLACEMENT_VERSION, apply_placement, default_paths, find_existing,
    node_key, placement_dict, read_placement, save_placement, split_key,
)
from storygraph.graph.models import Graph


def build(*nodes):
    """`(label, x, y, file)` → Graph；返回 `(graph, {label: node})`。"""
    g = Graph()
    made = {}
    for label, x, y, fp in nodes:
        nid = g.add_node(label, x, y)
        g.nodes[nid].file_path = fp
        made[label] = g.nodes[nid]
    return g, made


# ---------------------------------------------------------------------------
#  瘦格式
# ---------------------------------------------------------------------------

def test_placement_file_has_no_content(tmp_path):
    """版式文件里出现 operations / statements = 内容混进版式。"""
    g, _ = build(('ch01', 10, 20, 'game/ch01.rpy'))
    g.nodes[1].set_operations(['e "早上好"', 'return'])

    data = placement_dict(g, tmp_path)
    raw = json.dumps(data, ensure_ascii=False)

    assert 'operations' not in raw
    assert 'statements' not in raw
    assert '早上好' not in raw
    assert data['version'] == PLACEMENT_VERSION


def test_key_is_relative_path_plus_label(tmp_path):
    g, _ = build(('ch01', 0, 0, str(tmp_path / 'game' / 'ch01.rpy')))
    key = node_key(g.nodes[1], tmp_path)
    assert key == 'game/ch01.rpy::ch01'
    assert split_key(key) == ('game/ch01.rpy', 'ch01')


def test_node_without_file_has_no_fake_path():
    g, _ = build(('手建章', 0, 0, ''))
    assert node_key(g.nodes[1], None) == '::手建章'


def test_only_manual_edges_are_saved(tmp_path):
    g, ids = build(('A', 0, 0, 'game/a.rpy'), ('B', 0, 0, 'game/b.rpy'))
    g.add_edge(ids['A'].id, ids['B'].id, etype='jump', origin='source')
    g.add_edge(ids['A'].id, ids['B'].id, etype='call', origin='manual',
               option_text='去 B')

    data = placement_dict(g, tmp_path)
    assert len(data['edges']) == 1, '派生边不该进版式文件'
    assert data['edges'][0]['type'] == 'call'
    assert data['edges'][0]['option_text'] == '去 B'


# ---------------------------------------------------------------------------
#  贴到图上：不替换、不增删
# ---------------------------------------------------------------------------

def test_apply_keeps_new_nodes_and_moves_old_ones(tmp_path):
    """
    核心场景：第 1 天摆好 A/B，第 2 天新增 Z 再扫描。
    贴版式之后：Z 还在（自动坐标），A/B 回到原位。
    """
    g1, ids1 = build(('A', 10, 10, str(tmp_path / 'game' / 'a.rpy')),
                     ('B', 200, 10, str(tmp_path / 'game' / 'b.rpy')))
    saved = placement_dict(g1, tmp_path)

    # 第 2 天：Z 排在前面，id 全部串位
    g2, ids2 = build(('Z', 0, 0, str(tmp_path / 'game' / 'z.rpy')),
                     ('A', 0, 0, str(tmp_path / 'game' / 'a.rpy')),
                     ('B', 0, 0, str(tmp_path / 'game' / 'b.rpy')))
    res = apply_placement(g2, {'ok': True, **saved}, tmp_path)

    assert res['matched'] == 2
    assert ids2['Z'].id in g2.nodes, '新章不能被版式挤掉'
    assert (ids2['A'].x, ids2['A'].y) == (10, 10)
    assert (ids2['B'].x, ids2['B'].y) == (200, 10)
    assert (ids2['Z'].x, ids2['Z'].y) == (0, 0), '没记录过的新章保留自动排版坐标'


def test_missing_keys_are_reported_not_fatal(tmp_path):
    g, _ = build(('A', 0, 0, 'game/a.rpy'))
    data = {'ok': True, 'version': 2,
            'nodes': {'game/gone.rpy::旧章': {'x': 5, 'y': 5}},
            'edges': []}
    res = apply_placement(g, data, tmp_path)
    assert res['ok'] is True
    assert res['matched'] == 0
    assert res['missing'] == ['game/gone.rpy::旧章']


def test_manual_edges_come_back_without_duplicates(tmp_path):
    g, ids = build(('A', 0, 0, 'game/a.rpy'), ('B', 0, 0, 'game/b.rpy'))
    g.add_edge(ids['A'].id, ids['B'].id, etype='call', origin='manual',
               option_text='去 B')
    saved = placement_dict(g, tmp_path)

    # 重新扫描出来的图只有节点、没有草图边
    g2, ids2 = build(('A', 0, 0, 'game/a.rpy'), ('B', 0, 0, 'game/b.rpy'))
    res = apply_placement(g2, {'ok': True, **saved}, tmp_path)
    assert res['edges_added'] == 1
    assert len(g2.edges) == 1

    # 再贴一次不能变出第二条
    again = apply_placement(g2, {'ok': True, **saved}, tmp_path)
    assert again['edges_added'] == 0
    assert len(g2.edges) == 1


# ---------------------------------------------------------------------------
#  老格式：只读兼容
# ---------------------------------------------------------------------------

def test_legacy_full_graph_json_is_read_as_coords_only(tmp_path):
    """老文件里有整段 operations —— 读的时候必须丢掉，否则内容会盖住新扫描。"""
    legacy = {
        'nodes': {
            '1': {'label': 'ch01', 'x': 12, 'y': 34, 'file_path': 'game/ch01.rpy',
                  'operations': ['e "旧内容"'], 'statements': [{'kind': 'say'}]},
        },
        'edges': {},
        'next_ids': {'node': 2, 'edge': 1},
    }
    p = tmp_path / 'storygraph_layout.json'
    p.write_text(json.dumps(legacy, ensure_ascii=False), encoding='utf-8')

    data = read_placement(p)
    assert data['ok'] is True
    assert data['migrated'] is True
    assert data['nodes'] == {'game/ch01.rpy::ch01': {'x': 12.0, 'y': 34.0}}
    assert '旧内容' not in json.dumps(data, ensure_ascii=False)


def test_legacy_edges_without_origin_are_not_migrated(tmp_path):
    """老文件里没有 origin 的边全部来自解析 —— 存进版式只会和派生边打架。"""
    legacy = {
        'nodes': {'1': {'label': 'A', 'x': 0, 'y': 0, 'file_path': 'game/a.rpy'},
                  '2': {'label': 'B', 'x': 0, 'y': 0, 'file_path': 'game/b.rpy'}},
        'edges': {'1': {'from': 1, 'to': 2, 'type': 'jump'}},
    }
    p = tmp_path / 'storygraph_layout.json'
    p.write_text(json.dumps(legacy), encoding='utf-8')
    data = read_placement(p)
    assert data['edges'] == []


def test_legacy_absolute_path_is_normalized_to_project_relative(tmp_path):
    """
    真机踩过（2026-10-23）：老文件里 `file_path` 是**绝对路径**，
    不规范化的话，和扫描出来的相对路径**一条都对不上**（实测 0/1）——
    表现就是"版式白存了"。
    """
    legacy = {
        'nodes': {'1': {'label': 'ch01', 'x': 1, 'y': 2,
                        'file_path': str(tmp_path / 'game' / 'ch01.rpy')}},
        'edges': {},
    }
    p = tmp_path / 'storygraph_layout.json'
    p.write_text(json.dumps(legacy, ensure_ascii=False), encoding='utf-8')

    data = read_placement(p, tmp_path)
    assert list(data['nodes']) == ['game/ch01.rpy::ch01']


def test_new_format_keys_are_normalized_too(tmp_path):
    """新版文件也可能被人在别的机器上存成绝对路径 —— 读的时候一样要归一。"""
    p = tmp_path / '.storygraph' / 'layout.json'
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({
        'version': 2,
        'nodes': {f'{tmp_path / "game" / "a.rpy"}::A': {'x': 3, 'y': 4}},
        'edges': [],
    }, ensure_ascii=False), encoding='utf-8')

    data = read_placement(p, tmp_path)
    assert list(data['nodes']) == ['game/a.rpy::A']


def test_broken_file_is_reported_not_raised(tmp_path):
    p = tmp_path / 'layout.json'
    p.write_text('{ 这不是 JSON', encoding='utf-8')
    data = read_placement(p)
    assert data['ok'] is False
    assert data['error']


# ---------------------------------------------------------------------------
#  路径
# ---------------------------------------------------------------------------

def test_new_path_wins_but_legacy_is_found(tmp_path):
    new, old = default_paths(tmp_path)
    assert new.name == 'layout.json' and new.parent.name == '.storygraph'
    assert old.name == 'storygraph_layout.json'

    assert find_existing(tmp_path) is None
    old.write_text('{"nodes": {}}', encoding='utf-8')
    assert find_existing(tmp_path) == old

    new.parent.mkdir(parents=True)
    new.write_text('{"version": 2, "nodes": {}}', encoding='utf-8')
    assert find_existing(tmp_path) == new, '新版优先'


def test_save_creates_the_directory(tmp_path):
    g, _ = build(('A', 1, 2, 'game/a.rpy'))
    target = tmp_path / '.storygraph' / 'layout.json'
    res = save_placement(g, target, tmp_path)
    assert res['ok'] is True and target.is_file()
    again = read_placement(target)
    assert again['nodes'] == {'game/a.rpy::A': {'x': 1.0, 'y': 2.0}}
