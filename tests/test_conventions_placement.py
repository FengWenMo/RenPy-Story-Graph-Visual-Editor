# -*- coding: utf-8 -*-
"""
项目形状要能**被记住**：探测出来的 vs 清单里写的，必须分得开。

为什么要测这个（G0）：
    "我怎么知道用户怎么写" 的答案是"探测一次、给人确认、记进清单"。
    如果探测结果和清单声明混成一个字段，报告里就说不清
    "这是你说的还是我猜的" —— 那等于把猜测伪装成事实。
"""
from __future__ import annotations

import json

from storygraph.story.project_conventions import (
    detect_conventions, load_manifest,
)


def _project(tmp_path):
    game = tmp_path / 'game'
    game.mkdir()
    (game / 'controller.rpy').write_text(
        'label ch01_control:\n    call ch01 from _call_ch01_1\n    return\n',
        encoding='utf-8')
    (game / 'ch01.rpy').write_text(
        'default trust = 0\n\nlabel ch01:\n    "台词"\n    return\n',
        encoding='utf-8')
    return tmp_path


def test_detected_placement_is_marked_as_detected(tmp_path):
    root = _project(tmp_path)
    conv = detect_conventions(root)

    assert conv.placement_source == 'detected'
    assert conv.placement.get('control_flow') == 'game/controller.rpy'
    assert conv.placement.get('content_globs') == ['game/ch01.rpy']
    assert conv.placement.get('declaration_style') == 'dedicated'


def test_manifest_placement_overrides_detection(tmp_path):
    """清单是"人确认过的"，必须盖过探测 —— 人不该被工具反复纠正。"""
    root = _project(tmp_path)
    (root / 'storygraph.rules.json').write_text(json.dumps({
        'placement': {
            'control_flow': 'game/我自己说的.rpy',
            'declarations': 'game/我自己说的声明.rpy',
        }
    }, ensure_ascii=False), encoding='utf-8')

    conv = detect_conventions(root)
    assert conv.placement_source == 'declared'
    assert conv.placement['control_flow'] == 'game/我自己说的.rpy'
    assert conv.placement['declarations'] == 'game/我自己说的声明.rpy'
    # 清单没写的键，仍然用探测值（不清空）
    assert conv.placement['content_globs'] == ['game/ch01.rpy']


def test_placement_round_trips_in_to_dict(tmp_path):
    root = _project(tmp_path)
    conv = detect_conventions(root)
    data = conv.to_dict()
    assert 'placement' in data and 'placement_source' in data
    assert data['placement_source'] == 'detected'


def test_broken_manifest_still_gives_detected_placement(tmp_path):
    """清单坏了 → 当作没有清单，但形状照样探测出来（不能因为手写错就什么都不给）。"""
    root = _project(tmp_path)
    (root / 'storygraph.rules.json').write_text('{ 这不是 JSON', encoding='utf-8')

    data, path, warns = load_manifest(root)
    assert data == {} and warns

    conv = detect_conventions(root)
    assert conv.placement_source == 'detected'
    assert conv.placement.get('control_flow') == 'game/controller.rpy'


# ---------------------------------------------------------------------------
#  写进清单：必须由人显式触发，且只动 placement 段
# ---------------------------------------------------------------------------

def test_write_placement_records_the_four_keys(tmp_path):
    from storygraph.story.project_conventions import write_placement

    root = _project(tmp_path)
    res = write_placement(root)
    assert res['ok'], res.get('reason')

    data = json.loads((root / 'storygraph.rules.json').read_text(encoding='utf-8'))
    assert data['placement']['control_flow'] == 'game/controller.rpy'
    assert data['placement']['declaration_style'] == 'dedicated'
    assert set(data['placement']) == {
        'declarations', 'control_flow', 'content_globs', 'declaration_style'}


def test_write_placement_keeps_other_sections(tmp_path):
    """清单里别的段（statements / poses / 任意手写键）一个都不能丢。"""
    from storygraph.story.project_conventions import write_placement

    root = _project(tmp_path)
    manifest = root / 'storygraph.rules.json'
    manifest.write_text(json.dumps({
        'statements': {'mycue': {'label': '我的提示'}},
        'poses': {'e': ['happy']},
        '我自己加的': 42,
    }, ensure_ascii=False), encoding='utf-8')

    assert write_placement(root)['ok']

    data = json.loads(manifest.read_text(encoding='utf-8'))
    assert data['statements'] == {'mycue': {'label': '我的提示'}}
    assert data['poses'] == {'e': ['happy']}
    assert data['我自己加的'] == 42
    assert 'placement' in data


def test_write_placement_refuses_to_clobber_broken_json(tmp_path):
    from storygraph.story.project_conventions import write_placement

    root = _project(tmp_path)
    manifest = root / 'storygraph.rules.json'
    bad = '{ 这不是 JSON'
    manifest.write_text(bad, encoding='utf-8')

    res = write_placement(root)
    assert res['ok'] is False
    assert '拒绝覆盖' in res['reason']
    assert manifest.read_text(encoding='utf-8') == bad, '坏了就一个字节都不动'


def test_written_placement_reads_back_as_declared(tmp_path):
    """写进去之后，下次打开项目就该是 'declared'（人确认过），不是 'detected'。"""
    from storygraph.story.project_conventions import write_placement

    root = _project(tmp_path)
    write_placement(root)
    conv = detect_conventions(root)
    assert conv.placement_source == 'declared'
