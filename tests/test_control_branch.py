# -*- coding: utf-8 -*-
"""
条件 / 菜单分支**真的写进编排层**（不是草图）。

钉住四件事：

  · 写的是 `X_control` 里的一小段，**章节剧本一个字节都不动**；
  · 改 / 删按 `# storygraph-edge:<id>` 标记找，不按行号（行号会漂）；
  · 目标章缺包装时先补包装、再写分支，撤销一并回滚；
  · 计划之后文件又被改过 → **拒绝写**，不覆盖别人的新改动。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from storygraph.story.control_branch import (apply_control_branch,
                                            branch_marker,
                                            find_branch_range,
                                            plan_control_branch,
                                            plan_control_branch_remove)

GAME = 'game'
SCRIPT = 'game/script'


def _project(tmp_path, control=None):
    """最小工程：`game/script/ch01.rpy`、`ch02.rpy` + 可选的编排层。"""
    root = tmp_path / 'proj'
    (root / SCRIPT).mkdir(parents=True)
    (root / SCRIPT / 'ch01.rpy').write_text(
        'label ch01:\n    e "第一句"\n    return\n', encoding='utf-8')
    (root / SCRIPT / 'ch02.rpy').write_text(
        'label ch02:\n    e "第二句"\n    return\n', encoding='utf-8')
    if control is not None:
        (root / GAME / 'chapter_controller.rpy').write_text(
            control, encoding='utf-8')
    return root


def _control(root):
    return root / GAME / 'chapter_controller.rpy'


def _text(path):
    return Path(path).read_text(encoding='utf-8')


BASE = ('label ch01_control:\n    call ch01\n    return\n\n'
        'label ch02_control:\n    call ch02\n    return\n')


def test_inserts_condition_before_the_final_route(tmp_path):
    root = _project(tmp_path, BASE)
    script_before = _text(root / SCRIPT / 'ch01.rpy')
    plan = plan_control_branch(root, 'ch01', 'ch02', 'jump',
                               condition='score > 3', edge_id=7)
    assert plan.ok, plan.reason
    assert plan.kind == 'insert'
    assert apply_control_branch(plan)['ok']
    text = _text(_control(root))
    assert f'    {branch_marker(7)}' in text
    assert '    if score > 3:\n        jump ch02_control\n' in text
    # 条件不成立时仍然走原来的 return
    assert text.rstrip().endswith('return')
    # 章节剧本一个字都没动
    assert _text(root / SCRIPT / 'ch01.rpy') == script_before


def test_inserts_menu_option_with_condition(tmp_path):
    root = _project(tmp_path, BASE)
    plan = plan_control_branch(root, 'ch01', 'ch02', 'jump',
                               condition='flag', option_text='去屋顶',
                               edge_id=8)
    assert plan.ok, plan.reason
    assert apply_control_branch(plan)['ok']
    text = _text(_control(root))
    assert '    # storygraph-menu:ch01\n    menu:\n' in text
    assert '        # storygraph-edge:8\n' \
           '        "去屋顶" if flag:\n' \
           '            jump ch02_control\n' in text


def test_two_menu_edges_share_one_menu(tmp_path):
    """两条菜单边不能写成两个 menu（第一个选项永远先执行，第二个成死线）。"""
    root = _project(tmp_path, (
        'label ch01_control:\n    call ch01\n    return\n\n'
        'label ch02_control:\n    call ch02\n    return\n\n'
        'label ch03_control:\n    call ch03\n    return\n'))
    (root / SCRIPT / 'ch03.rpy').write_text(
        'label ch03:\n    return\n', encoding='utf-8')
    first = plan_control_branch(root, 'ch01', 'ch02', 'jump',
                                option_text='去屋顶', edge_id=21)
    assert first.ok and apply_control_branch(first)['ok']
    second = plan_control_branch(root, 'ch01', 'ch03', 'jump',
                                 option_text='留在教室', edge_id=22)
    assert second.ok, second.reason
    assert apply_control_branch(second)['ok']
    text = _text(_control(root))
    assert text.count('\n    menu:\n') == 1, text
    assert text.count('storygraph-menu:ch01') == 1
    assert '去屋顶' in text and '留在教室' in text


def test_edit_replaces_the_marked_block_only(tmp_path):
    root = _project(tmp_path, BASE)
    first = plan_control_branch(root, 'ch01', 'ch02', 'jump',
                                option_text='去屋顶', edge_id=9)
    assert first.ok and apply_control_branch(first)['ok']
    before = _text(_control(root))
    again = plan_control_branch(root, 'ch01', 'ch02', 'jump',
                                condition='flag', option_text='去天台',
                                edge_id=9)
    assert again.ok, again.reason
    assert again.kind == 'replace'
    assert apply_control_branch(again)['ok']
    text = _text(_control(root))
    assert '去屋顶' not in text, '旧选项没有换掉'
    assert text.count('storygraph-edge:9') == 1, '标记段重复了'
    assert '"去天台" if flag:' in text
    # 段外的内容一行没变
    assert 'label ch01_control:\n    call ch01\n' in text
    assert before.startswith('label ch01_control:\n    call ch01\n')


def test_plain_control_route_is_replaced_by_a_menu(tmp_path):
    """普通路由加菜单项：旧 `jump` 必须消失，否则它无条件先执行。"""
    root = _project(tmp_path, (
        'label ch01_control:\n    call ch01\n    jump ch02_control\n\n'
        'label ch02_control:\n    call ch02\n    return\n'))
    plan = plan_control_branch(root, 'ch01', 'ch02', 'jump',
                               option_text='去屋顶', edge_id=23,
                               replace_dst_label='ch02', replace_etype='jump')
    assert plan.ok, plan.reason
    assert apply_control_branch(plan)['ok']
    text = _text(_control(root))
    assert '\n    jump ch02_control\n' not in '\n' + text, text
    assert text.count('\n    menu:\n') == 1, text
    assert '去屋顶' in text


def test_if_branch_cannot_be_silently_turned_into_a_menu(tmp_path):
    """`if` 分支改成菜单会多一次玩家选择 —— 拒绝，让人删了重接。"""
    root = _project(tmp_path, BASE)
    first = plan_control_branch(root, 'ch01', 'ch02', 'jump',
                                condition='flag', edge_id=19)
    assert first.ok and apply_control_branch(first)['ok']
    again = plan_control_branch(root, 'ch01', 'ch02', 'jump',
                                condition='flag', option_text='去屋顶',
                                edge_id=19)
    assert again.ok is False
    assert '菜单' in again.reason


def test_remove_one_menu_option_keeps_the_other(tmp_path):
    root = _project(tmp_path, (
        'label ch01_control:\n    call ch01\n    return\n\n'
        'label ch02_control:\n    call ch02\n    return\n\n'
        'label ch03_control:\n    call ch03\n    return\n'))
    (root / SCRIPT / 'ch03.rpy').write_text(
        'label ch03:\n    return\n', encoding='utf-8')
    for target, opt, eid in (('ch02', '去屋顶', 31), ('ch03', '留下', 32)):
        assert apply_control_branch(plan_control_branch(
            root, 'ch01', target, 'jump', option_text=opt,
            edge_id=eid))['ok']
    rm = plan_control_branch_remove(root, 'ch01', 31)
    assert rm.ok, rm.reason
    assert apply_control_branch(rm)['ok']
    text = _text(_control(root))
    assert '去屋顶' not in text
    assert '留下' in text
    assert 'storygraph-menu:ch01' in text
    # 删掉最后一个选项 → 整个菜单组一起走
    rm2 = plan_control_branch_remove(root, 'ch01', 32)
    assert rm2.ok and apply_control_branch(rm2)['ok']
    text = _text(_control(root))
    assert 'menu:' not in text and 'storygraph-menu' not in text


def test_remove_command_deletes_the_option_and_undo_restores_it(tmp_path):
    from storygraph.graph.commands import RemoveControlBranchCommand
    from storygraph.graph.models import Graph

    root = _project(tmp_path, (
        'label ch01_control:\n    call ch01\n    return\n\n'
        'label ch02_control:\n    call ch02\n    return\n\n'
        'label ch03_control:\n    call ch03\n    return\n'))
    (root / SCRIPT / 'ch03.rpy').write_text(
        'label ch03:\n    return\n', encoding='utf-8')
    for target, opt, eid in (('ch02', '去屋顶', 41), ('ch03', '留下', 42)):
        assert apply_control_branch(plan_control_branch(
            root, 'ch01', target, 'jump', option_text=opt,
            edge_id=eid))['ok']

    class Host:
        def __init__(self):
            self.graph = Graph()
            self.changed = 0

        def on_graph_structure_changed(self):
            self.changed += 1

    host = Host()
    before = _text(_control(root))
    bplan = plan_control_branch_remove(root, 'ch01', 41)
    assert bplan.ok
    cmd = RemoveControlBranchCommand(host, bplan, 41)
    cmd.execute()
    assert '去屋顶' not in _text(_control(root))
    assert '留下' in _text(_control(root))
    cmd.undo()
    assert _text(_control(root)) == before


def test_write_command_turns_a_plain_route_into_a_menu(tmp_path):
    from storygraph.graph.commands import WriteControlBranchCommand
    from storygraph.graph.models import Graph

    root = _project(tmp_path, (
        'label ch01_control:\n    call ch01\n    jump ch02_control\n\n'
        'label ch02_control:\n    call ch02\n    return\n'))

    class Host:
        def __init__(self):
            self.graph = Graph()
            self.changed = 0

        def on_graph_structure_changed(self):
            self.changed += 1

    host = Host()
    a = host.graph.add_node('ch01', 0, 0)
    b = host.graph.add_node('ch02', 0, 0)
    eid = host.graph.add_edge(a, b, 'jump', '', '',
                              origin='control', position='control_end')
    before = _text(_control(root))
    cmd = WriteControlBranchCommand(host, root, eid, 'jump', '',
                                    '去屋顶')
    cmd.execute()
    text = _text(_control(root))
    assert '\n    jump ch02_control\n' not in '\n' + text, text
    assert '"去屋顶":' in text
    cmd.undo()
    assert _text(_control(root)) == before


def test_same_input_is_a_noop(tmp_path):
    root = _project(tmp_path, BASE)
    first = plan_control_branch(root, 'ch01', 'ch02', 'jump',
                                condition='score > 3', edge_id=10)
    assert apply_control_branch(first)['ok']
    again = plan_control_branch(root, 'ch01', 'ch02', 'jump',
                                condition='score > 3', edge_id=10)
    assert again.ok and again.kind == 'exists'
    assert apply_control_branch(again)['ok']


def test_remove_keeps_everything_else(tmp_path):
    root = _project(tmp_path, BASE)
    first = plan_control_branch(root, 'ch01', 'ch02', 'jump',
                                condition='score > 3', edge_id=11)
    assert apply_control_branch(first)['ok']
    rm = plan_control_branch_remove(root, 'ch01', 11)
    assert rm.ok, rm.reason
    assert apply_control_branch(rm)['ok']
    assert _text(_control(root)) == BASE


def test_insert_after_a_trailing_menu_block(tmp_path):
    """`X_control` 以 menu 收尾时，插在菜单块**之后**，不插进选项体。"""
    root = _project(tmp_path, (
        'label ch01_control:\n    call ch01\n    menu:\n'
        '        "走":\n            jump ch02_control\n\n'
        'label ch02_control:\n    call ch02\n    return\n'))
    plan = plan_control_branch(root, 'ch01', 'ch02', 'jump',
                               condition='flag', edge_id=12)
    assert plan.ok, plan.reason
    assert apply_control_branch(plan)['ok']
    text = _text(_control(root))
    lines = text.splitlines()
    menu_at = lines.index('    menu:')
    branch_at = next(i for i, ln in enumerate(lines)
                     if branch_marker(12) in ln)
    assert branch_at > menu_at + 2, '分支插进菜单体里了'
    # 组后面一条顶层语句都没有 → 条件不成立时必须自己结束
    assert '    return' in lines[branch_at + 1:branch_at + 6]


def test_apply_refuses_when_the_file_changed_after_plan(tmp_path):
    root = _project(tmp_path, BASE)
    plan = plan_control_branch(root, 'ch01', 'ch02', 'jump',
                               condition='flag', edge_id=13)
    assert plan.ok
    path = _control(root)
    path.write_text(BASE + '\n# 别处的新改动\n', encoding='utf-8')
    res = apply_control_branch(plan)
    assert res['ok'] is False
    assert '又被改过' in res['error']
    assert '# 别处的新改动' in _text(path), '拒绝写时不该动文件'


def test_no_control_file_is_explained(tmp_path):
    root = _project(tmp_path, None)
    plan = plan_control_branch(root, 'ch01', 'ch02', 'jump',
                               condition='flag', edge_id=14)
    assert plan.ok is False
    assert '编排层' in plan.reason


def test_missing_source_wrapper_is_explained(tmp_path):
    root = _project(tmp_path, 'label other_control:\n    return\n')
    plan = plan_control_branch(root, 'ch01', 'ch02', 'jump',
                               condition='flag', edge_id=15)
    assert plan.ok is False
    assert 'ch01_control' in plan.reason


def test_missing_target_wrapper_carries_an_init_plan(tmp_path):
    root = _project(tmp_path, 'label ch01_control:\n    call ch01\n    return\n')
    # 目标章 ch02 还没有包装
    plan = plan_control_branch(root, 'ch01', 'ch02', 'jump',
                               condition='flag', edge_id=16)
    assert plan.ok is False
    assert plan.init is not None and plan.init.ok
    assert 'ch02_control' in plan.reason


def test_chapter_ending_with_a_jump_is_refused(tmp_path):
    """
    章末是一条 `jump` 时，`X_control` 的 `call X` 永远不返回 ——
    分支写进去不会执行，必须当场拒绝，不能给一条"看起来接好了"的线。
    """
    root = _project(tmp_path, BASE)
    (root / SCRIPT / 'ch01.rpy').write_text(
        'label ch01:\n    e "第一句"\n    jump ch02\n', encoding='utf-8')
    plan = plan_control_branch(root, 'ch01', 'ch02', 'jump',
                               condition='flag', edge_id=17)
    assert plan.ok is False
    assert '永远不会返回' in plan.reason


def test_a_condition_that_would_break_the_line_is_refused(tmp_path):
    root = _project(tmp_path, BASE)
    plan = plan_control_branch(root, 'ch01', 'ch02', 'jump',
                               condition='flag:', edge_id=24)
    assert plan.ok is False
    assert '冒号' in plan.reason


def test_edge_id_zero_is_not_a_valid_marker(tmp_path):
    """0 号标记不与"没有 id"混淆：渲染时仍写成显式 0。"""
    root = _project(tmp_path, BASE)
    plan = plan_control_branch(root, 'ch01', 'ch02', 'jump',
                               condition='flag', edge_id=0)
    assert plan.ok
    assert find_branch_range('\n'.join(plan.new_lines), 0) is not None
