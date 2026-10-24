# -*- coding: utf-8 -*-
"""
**接管进入点**：`jump X` → `jump X_control` + 补 `X_control` 包装。

为什么它是"真问题"而不是洁癖：`X_control` 要有**入口**才执行。项目里原来
写 `jump X`，补一个没人跳进来的 `X_control` 只会是死代码 —— 图上的线看起来
接好了，游戏里那条路永远不走。

这里钉住：多文件一起改、写前核对、失败不半写、撤销逐字节还原、
`call X` 不改（包裝可能 jump 走，调用点永远回不来）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from storygraph.graph.commands import TakeoverCommand
from storygraph.story.control_takeover import (apply_takeover,
                                               plan_takeover)


def _project(tmp_path, control=None, scripts=None, newline='\n', bom=False):
    root = tmp_path / 'proj'
    (root / 'game' / 'script').mkdir(parents=True)
    files = scripts or {
        'ch01.rpy': 'label ch01:\n    e "a"\n    jump ch02\n',
        'ch02.rpy': 'label ch02:\n    e "b"\n    return\n',
    }
    for name, body in files.items():
        path = root / 'game' / 'script' / name
        data = body.replace('\n', newline).encode('utf-8')
        if bom:
            data = b'\xef\xbb\xbf' + data
        path.write_bytes(data)
    if control is not None:
        (root / 'game' / 'chapter_controller.rpy').write_text(
            control, encoding='utf-8')
    return root


def _text(path):
    return Path(path).read_text(encoding='utf-8')


BASE = 'label ch01_control:\n    call ch01\n    return\n'


def test_rewrites_jumps_and_adds_missing_wrappers(tmp_path):
    root = _project(tmp_path, BASE)
    plan = plan_takeover(root, ['ch01', 'ch02'])
    assert plan.ok, plan.blocking
    assert plan.wrappers == ['ch02']
    assert len(plan.edits) == 2
    assert any('jump ch02_control' in e.new_text for e in plan.edits)
    # diff 里能看到改的那一行
    assert '-    jump ch02' in plan.diff
    assert '+    jump ch02_control' in plan.diff
    res = apply_takeover(plan)
    assert res['ok'], res
    assert 'jump ch02_control' in _text(root / 'game' / 'script' / 'ch01.rpy')
    assert 'label ch02_control:' in _text(
        root / 'game' / 'chapter_controller.rpy')


def test_call_is_reported_not_rewritten(tmp_path):
    root = _project(tmp_path, BASE, scripts={
        'ch01.rpy': 'label ch01:\n    call ch02\n    return\n',
        'ch02.rpy': 'label ch02:\n    e "b"\n    return\n',
    })
    plan = plan_takeover(root, ['ch01', 'ch02'])
    assert plan.ok
    assert any('call ch02' in note for note in plan.notes)
    assert 'ch02_control' not in _text(root / 'game' / 'script' / 'ch01.rpy')


def test_refuses_when_a_file_changed_after_plan(tmp_path):
    root = _project(tmp_path, BASE)
    plan = plan_takeover(root, ['ch01', 'ch02'])
    assert plan.ok
    target = root / 'game' / 'script' / 'ch01.rpy'
    target.write_text('label ch01:\n    e "改过了"\n    jump ch02\n',
                      encoding='utf-8')
    res = apply_takeover(plan)
    assert res['ok'] is False
    assert '一个文件都没写' in res['error']
    # 控制流文件也一个字没写（不能半写）
    assert _text(root / 'game' / 'chapter_controller.rpy') == BASE


def test_undo_restores_every_file_byte_for_byte(tmp_path):
    root = _project(tmp_path, BASE)
    before = {p: p.read_bytes() for p in root.rglob('*.rpy')}
    plan = plan_takeover(root, ['ch01', 'ch02'])
    host = type('Host', (), {
        'on_graph_structure_changed': lambda self: None})()
    cmd = TakeoverCommand(host, plan)
    cmd.execute()
    assert any(p.read_bytes() != before[p] for p in before)
    cmd.undo()
    assert {p: p.read_bytes() for p in root.rglob('*.rpy')} == before


def test_creates_the_control_file_when_there_is_none(tmp_path):
    root = _project(tmp_path, None)
    plan = plan_takeover(root, ['ch01', 'ch02'])
    assert plan.ok, plan.blocking
    assert plan.creates_control is True
    assert apply_takeover(plan)['ok']
    path = root / 'game' / 'chapter_controller.rpy'
    text = path.read_text(encoding='utf-8')
    assert 'label ch01_control:' in text
    assert 'label ch02_control:' in text
    assert 'jump ch02_control' in _text(root / 'game' / 'script' / 'ch01.rpy')


def test_undo_removes_a_control_file_that_takeover_created(tmp_path):
    """接管新建的编排层，撤销后不该留一个空 .rpy。"""
    root = _project(tmp_path, None)
    plan = plan_takeover(root, ['ch02'])
    assert plan.ok and plan.creates_control
    host = type('Host', (), {
        'on_graph_structure_changed': lambda self: None})()
    cmd = TakeoverCommand(host, plan)
    cmd.execute()
    path = root / 'game' / 'chapter_controller.rpy'
    assert path.exists()
    cmd.undo()
    assert not path.exists(), '撤销留下了接管新建的空编排层'


def test_keeps_crlf_and_bom(tmp_path):
    root = _project(tmp_path, None, newline='\r\n', bom=True)
    plan = plan_takeover(root, ['ch02'])
    assert plan.ok
    assert apply_takeover(plan)['ok']
    raw = (root / 'game' / 'script' / 'ch01.rpy').read_bytes()
    assert raw.startswith(b'\xef\xbb\xbf'), 'BOM 丢了'
    assert b'jump ch02_control\r\n' in raw, '换行风格没还原'


def test_missing_label_is_refused(tmp_path):
    root = _project(tmp_path, BASE)
    plan = plan_takeover(root, ['nope'])
    assert plan.ok is False
    assert '找不到' in plan.reason


def test_no_entry_is_a_note_not_a_silent_success(tmp_path):
    root = _project(tmp_path, BASE, scripts={
        'ch01.rpy': 'label ch01:\n    e "a"\n    return\n',
        'ch02.rpy': 'label ch02:\n    e "b"\n    return\n',
    })
    plan = plan_takeover(root, ['ch02'])
    assert plan.ok
    assert any('入口都没找到' in note for note in plan.notes)


def test_chinese_labels_are_supported(tmp_path):
    """Ren'Py 的 label 允许中文（真机 `第一章`），接管不能只认 ASCII。"""
    root = _project(tmp_path, None, scripts={
        'ch01.rpy': 'label 第一章:\n    e "a"\n    jump 第二章\n',
        'ch02.rpy': 'label 第二章:\n    e "b"\n    return\n',
    })
    plan = plan_takeover(root, ['第一章', '第二章'])
    assert plan.ok, plan.blocking
    assert apply_takeover(plan)['ok']
    text = _text(root / 'game' / 'script' / 'ch01.rpy')
    assert 'jump 第二章_control' in text
