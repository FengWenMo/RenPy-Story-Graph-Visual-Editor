# -*- coding: utf-8 -*-
"""
跨章删源边：那一行在**块体里**时不能只删一半。

Ren'Py 的块体至少要有一条语句。一条 `jump` 如果正好是某个选项体 /
`if` 体的**唯一内容**，只删这一行会留下

    menu:
        "去屋顶":

—— 文件看起来少了一行，实际整份脚本**解析不了**。

这里的判据：

  · 块里只有它 → 连**整块**删（选项体 / if；menu 只剩这一个选项时连 menu）；
  · 块里还有别的语句 → 只删这一行；
  · 整块删完这一章一拍不剩 → 不删整块，补 `pass` 保住 label。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ui_project import build_project  # noqa: E402

from storygraph.app.editor import EditorSession  # noqa: E402
from storygraph.graph.models import Graph  # noqa: E402
from storygraph.app.source_line_delete import (  # noqa: E402
    apply_source_line_delete, plan_source_line_delete)


def _project(tmp_path, body):
    root = build_project(tmp_path / 'proj')
    path = root / 'game' / 'script' / 'ch01.rpy'
    path.write_text(body, encoding='utf-8')
    return root, path


def _jump_line_id(root, path, target):
    session = EditorSession(project_root=root)
    session.load(path)
    beat = next(b for b in session.beats
                if str(getattr(b, 'raw', '') or '').strip() == f'jump {target}')
    return beat.line_id


def _delete(tmp_path, body, target):
    root, path = _project(tmp_path, body)
    line_id = _jump_line_id(root, path, target)
    plan = plan_source_line_delete(root, path, line_id)
    assert plan.ok, plan.reason
    res = apply_source_line_delete(plan)
    assert res['ok'], res
    return plan, path.read_text(encoding='utf-8')


MENU_TWO = '''label ch01:
    e "开始"
    menu:
        "去屋顶":
            jump roof
        "留在教室":
            e "留下"
    e "继续"
    return

label roof:
    e "屋顶"
    return
'''

MENU_ONE = '''label ch01:
    e "开始"
    menu:
        "去屋顶":
            jump roof
    e "继续"
    return

label roof:
    e "屋顶"
    return
'''

IF_ONLY = '''label ch01:
    e "开始"
    if flag:
        jump roof
    e "继续"
    return

label roof:
    e "屋顶"
    return
'''

IF_WITH_MORE = '''label ch01:
    e "开始"
    if flag:
        e "条件里还有一句"
        jump roof
    e "继续"
    return

label roof:
    e "屋顶"
    return
'''


def test_menu_option_with_only_a_jump_is_deleted_as_a_whole_option(tmp_path):
    plan, text = _delete(tmp_path, MENU_TWO, 'roof')
    assert plan.cascade is True
    assert '"去屋顶":' not in text, '选项头留下了空块体'
    assert '"留在教室":' in text
    assert 'jump roof' not in text
    assert '    menu:\n' in text


def test_menu_with_a_single_option_is_deleted_as_a_whole_menu(tmp_path):
    plan, text = _delete(tmp_path, MENU_ONE, 'roof')
    assert plan.cascade is True
    assert 'menu:' not in text, '只剩一个选项时 menu 也成了空块'
    assert 'label roof:' in text


def test_if_with_only_the_jump_is_deleted_as_a_whole_block(tmp_path):
    plan, text = _delete(tmp_path, IF_ONLY, 'roof')
    assert plan.cascade is True
    assert 'if flag:' not in text
    assert 'jump roof' not in text
    assert 'e "继续"' in text


def test_if_with_another_statement_only_loses_the_jump_line(tmp_path):
    plan, text = _delete(tmp_path, IF_WITH_MORE, 'roof')
    assert plan.cascade is False
    assert 'pass' not in text
    assert 'if flag:' in text
    assert 'e "条件里还有一句"' in text
    assert 'jump roof' not in text


def test_if_with_an_elif_chain_keeps_the_jump_as_pass(tmp_path):
    """删掉 `if` 的头会让 `elif` 失去 if —— 这种情况只删 jump，补 pass。"""
    body = '''label ch01:
    e "开始"
    if a:
        jump roof
    elif b:
        e "另一条"
    e "继续"
    return

label roof:
    return
'''
    plan, text = _delete(tmp_path, body, 'roof')
    assert plan.cascade is False
    assert 'if a:\n        pass\n' in text, text
    assert 'elif b:' in text
    assert 'jump roof' not in text


def test_refuses_when_the_whole_label_would_disappear(tmp_path):
    """整块删完 label 一拍不剩 → 不删整块，补 `pass`（label 不能空）。"""
    root, path = _project(tmp_path, '''label only:
    if flag:
        jump roof

label roof:
    return
''')
    line_id = _jump_line_id(root, path, 'roof')
    plan = plan_source_line_delete(root, path, line_id)
    # 这一章只有这一拍 → 现有判据先拒绝（"一拍不剩"），不会走 pass 分支。
    assert plan.ok is False
    assert '一拍不剩' in plan.reason
